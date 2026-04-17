from fastapi import APIRouter, UploadFile, File, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db_session

import shutil
import os
import uuid
import ezdxf
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt

from app.models.cad_conversion import CADConversion
from app.schemas.cad_conversion import CADConversionOut


router = APIRouter(prefix="/cad", tags=["CAD"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# -------------------- FORMAT --------------------
def detect_columns(df):
    if {"easting", "northing"}.issubset(df.columns):
        return "xy"
    elif {"x", "y"}.issubset(df.columns):
        return "xy"
    else:
        raise ValueError("CSV must contain easting/northing or x/y")


# 🔥 FIXED (SAFE PARSING)
def get_coordinates(row, mode):
    try:
        x = float(row.get("easting") or row.get("x"))
        y = float(row.get("northing") or row.get("y"))
        return x, y
    except Exception:
        return None


# -------------------- NORMALIZE + CENTER --------------------
def normalize_and_center(points):
    min_x = min(p[0] for p in points)
    min_y = min(p[1] for p in points)

    shifted = [(x - min_x, y - min_y) for x, y in points]

    max_x = max(p[0] for p in shifted)
    max_y = max(p[1] for p in shifted)

    cx = max_x / 2
    cy = max_y / 2

    return [(x - cx, y - cy) for x, y in shifted]


# -------------------- BOUNDING BOX --------------------
def add_bounding_box(msp, points):
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)

    pad = 10

    msp.add_lwpolyline([
        (min_x - pad, min_y - pad),
        (max_x + pad, min_y - pad),
        (max_x + pad, max_y + pad),
        (min_x - pad, max_y + pad),
        (min_x - pad, min_y - pad)
    ], dxfattribs={"color": 3})


# -------------------- TITLE BLOCK --------------------
def add_title_block(msp, base_x, base_y, project_name, area):
    w, h = 80, 40

    msp.add_lwpolyline([
        (base_x, base_y),
        (base_x + w, base_y),
        (base_x + w, base_y + h),
        (base_x, base_y + h),
        (base_x, base_y)
    ], dxfattribs={"color": 4})

    rh = h / 4

    for i in range(1, 4):
        y = base_y + i * rh
        msp.add_line((base_x, y), (base_x + w, y), dxfattribs={"color": 4})

    pad_x = 4
    pad_y = rh / 3

    msp.add_text(f"Project: {project_name}", dxfattribs={"height": 3}).set_placement((base_x + pad_x, base_y + h - rh + pad_y))
    msp.add_text(f"Date: {datetime.now().date()}", dxfattribs={"height": 3}).set_placement((base_x + pad_x, base_y + h - 2*rh + pad_y))
    msp.add_text(f"Area: {area:.2f}", dxfattribs={"height": 3}).set_placement((base_x + pad_x, base_y + h - 3*rh + pad_y))
    msp.add_text("Scale: 1:1", dxfattribs={"height": 3}).set_placement((base_x + pad_x, base_y + pad_y))


# -------------------- PREVIEW --------------------
def generate_preview(points, output_path):
    x = [p[0] for p in points]
    y = [p[1] for p in points]

    plt.figure(figsize=(6, 6))
    plt.scatter(x, y, s=5)
    plt.axis('equal')
    plt.grid(True)

    path = output_path.replace(".dxf", ".png")
    plt.savefig(path)
    plt.close()

    return path


# -------------------- MAIN --------------------
async def csv_to_dxf(file_path: str, db: AsyncSession):
    # 🔥 FIXED CSV READ
    df = pd.read_csv(file_path, skipinitialspace=True)
    df.columns = df.columns.str.strip().str.lower()
    df = df.dropna()

    mode = detect_columns(df)

    raw = []
    for _, row in df.iterrows():
        coords = get_coordinates(row, mode)
        if coords:
            raw.append(coords)

    if not raw:
        raise ValueError("No valid points found")

    points = normalize_and_center(raw)

    doc = ezdxf.new()
    msp = doc.modelspace()

    doc.header["$PDMODE"] = 0
    doc.header["$PDSIZE"] = 0

    for x, y in points:
        msp.add_circle((x, y), radius=0.5)

    add_bounding_box(msp, points)

    max_x = max(p[0] for p in points)
    max_y = max(p[1] for p in points)

    add_title_block(msp, max_x + 20, max_y + 20, "Survey Project", 0)

    out = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.dxf").replace("\\", "/")
    doc.saveas(out)

    generate_preview(points, out)

    db.add(CADConversion(project_name="Survey", file_path=out, area=0))
    await db.commit()

    return out


# -------------------- API --------------------
@router.post("/csv-to-dxf")
async def convert(file: UploadFile = File(...), db: AsyncSession = Depends(get_db_session)):
    temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.csv")

    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        dxf_path = await csv_to_dxf(temp_path, db)
    finally:
        os.remove(temp_path)

    return FileResponse(
        path=dxf_path,
        media_type="application/dxf",
        filename="output.dxf"
    )


@router.get("/logs", response_model=list[CADConversionOut])
async def logs(db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(select(CADConversion))
    return res.scalars().all()