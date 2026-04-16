from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db_session

import shutil
import os
import uuid
import ezdxf
import pandas as pd
from datetime import datetime
import math
import zipfile
from io import BytesIO

from app.models.cad_conversion import CADConversion
from app.schemas.cad_conversion import CADConversionOut


router = APIRouter(prefix="/cad", tags=["CAD"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# -------------------- FORMAT DETECTION --------------------
def detect_columns(df):
    if {"latitude", "longitude"}.issubset(df.columns):
        return "latlon"
    elif {"lat", "lon"}.issubset(df.columns):
        return "latlon"
    elif {"easting", "northing"}.issubset(df.columns):
        return "xy"
    elif {"x", "y"}.issubset(df.columns):
        return "xy"
    else:
        raise ValueError("Invalid CSV format")


def get_coordinates(row, mode):
    if mode == "latlon":
        return float(row.get("longitude") or row.get("lon")), float(row.get("latitude") or row.get("lat"))
    return float(row.get("easting") or row.get("x")), float(row.get("northing") or row.get("y"))


# -------------------- AUTO SORT --------------------
def sort_points(points):
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


# -------------------- AREA --------------------
def calculate_area(points):
    area = 0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        area += (x1 * y2 - x2 * y1)
    return abs(area) / 2


# -------------------- VALIDATION --------------------
def is_valid_polygon(points):
    return len(points) >= 3


# -------------------- GRID --------------------
def add_grid(msp, base_x, base_y):
    for i in range(0, 100, 10):
        msp.add_line((base_x + i, base_y), (base_x + i, base_y + 100), dxfattribs={"color": 9})
        msp.add_line((base_x, base_y + i), (base_x + 100, base_y + i), dxfattribs={"color": 9})


# -------------------- MAIN LOGIC --------------------
async def csv_to_dxf(file_path: str, db: AsyncSession, project_name="Survey Project") -> str:
    df = pd.read_csv(file_path)
    mode = detect_columns(df)

    doc = ezdxf.new()
    msp = doc.modelspace()

    doc.layers.new("POINTS", dxfattribs={"color": 1})
    doc.layers.new("LABELS", dxfattribs={"color": 3})
    doc.layers.new("LINES", dxfattribs={"color": 5})
    doc.layers.new("META", dxfattribs={"color": 4})

    raw_points = []
    named_points = []

    for i, row in df.iterrows():
        try:
            x, y = get_coordinates(row, mode)
            name = str(row.get("name") or f"P{i+1}")
            raw_points.append((x, y))
            named_points.append((x, y, name))
        except:
            continue

    if not raw_points:
        raise ValueError("No valid points found")

    points = sort_points(raw_points)

    if not is_valid_polygon(points):
        raise ValueError("Need at least 3 valid points")

    for x, y, name in named_points:
        msp.add_circle((x, y), radius=2, dxfattribs={"layer": "POINTS"})
        msp.add_line((x - 2, y), (x + 2, y), dxfattribs={"layer": "POINTS"})
        msp.add_line((x, y - 2), (x, y + 2), dxfattribs={"layer": "POINTS"})

        msp.add_text(name, dxfattribs={"height": 4, "layer": "LABELS"}) \
            .set_placement((x + 3, y + 3))

        msp.add_text(f"{x:.2f},{y:.2f}", dxfattribs={"height": 3, "layer": "LABELS"}) \
            .set_placement((x + 3, y - 3))

    if points[0] != points[-1]:
        points.append(points[0])

    msp.add_lwpolyline(points, dxfattribs={"layer": "LINES"})

    area = calculate_area(points)

    add_grid(msp, points[0][0] - 50, points[0][1] - 50)

    base_x, base_y = points[0]

    msp.add_text(
        f"Project: {project_name}",
        dxfattribs={"height": 5, "layer": "META", "color": 4, "lineweight": 50}
    ).set_placement((base_x + 80, base_y + 40))

    msp.add_text(
        f"Date: {datetime.now().date()}",
        dxfattribs={"height": 5, "layer": "META", "color": 4, "lineweight": 50}
    ).set_placement((base_x + 80, base_y + 32))

    msp.add_text(
        f"Area: {area:.2f} sq.units",
        dxfattribs={"height": 5, "layer": "META", "color": 4, "lineweight": 50}
    ).set_placement((base_x + 80, base_y + 24))

    msp.add_text(
        "Scale: 1:1",
        dxfattribs={"height": 5, "layer": "META", "color": 4, "lineweight": 50}
    ).set_placement((base_x + 80, base_y + 16))

    output_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.dxf")
    doc.saveas(output_path)

    db_obj = CADConversion(
        project_name=project_name,
        file_path=output_path,
        area=area
    )
    db.add(db_obj)
    await db.commit()

    return output_path


# -------------------- MULTI FILE --------------------
@router.post("/upload-multiple")
async def convert_multiple(files: list[UploadFile] = File(...), db: AsyncSession = Depends(get_db_session)):
    outputs = []

    for file in files:
        temp_path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4()}.csv")

        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            dxf_path = await csv_to_dxf(temp_path, db, project_name=file.filename)
            outputs.append(dxf_path)
        finally:
            os.remove(temp_path)

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for path in outputs:
            zip_file.write(path, os.path.basename(path))

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=drawings.zip"}
    )


# -------------------- SINGLE FILE --------------------
@router.post("/csv-to-dxf")
async def convert(file: UploadFile = File(...), db: AsyncSession = Depends(get_db_session)):
    temp_path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4()}.csv")

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        dxf_path = await csv_to_dxf(temp_path, db)
    finally:
        os.remove(temp_path)

    return FileResponse(dxf_path, media_type="application/dxf", filename="output.dxf")


# -------------------- LOGS --------------------
@router.get("/logs", response_model=list[CADConversionOut])
async def get_logs(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(CADConversion).order_by(CADConversion.id.desc()))
    return result.scalars().all()