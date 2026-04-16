from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
import shutil
import os
import uuid
import ezdxf
import pandas as pd
from datetime import datetime
import math

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
        msp.add_line((base_x + i, base_y), (base_x + i, base_y + 100), dxfattribs={"color": 8})
        msp.add_line((base_x, base_y + i), (base_x + 100, base_y + i), dxfattribs={"color": 8})


# -------------------- MAIN LOGIC --------------------
def csv_to_dxf(file_path: str, project_name="Survey Project") -> str:
    df = pd.read_csv(file_path)
    mode = detect_columns(df)

    doc = ezdxf.new()
    msp = doc.modelspace()

    # Layers
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

    # ✅ AUTO SORT
    points = sort_points(raw_points)

    # ✅ VALIDATION
    if not is_valid_polygon(points):
        raise ValueError("Need at least 3 valid points")

    # ---------------- DRAW ----------------
    for x, y, name in named_points:

        # Better visuals
        msp.add_circle((x, y), radius=2, dxfattribs={"layer": "POINTS"})

        # Cross
        msp.add_line((x - 2, y), (x + 2, y), dxfattribs={"layer": "POINTS"})
        msp.add_line((x, y - 2), (x, y + 2), dxfattribs={"layer": "POINTS"})

        # Labels
        msp.add_text(name, dxfattribs={"height": 3, "layer": "LABELS"}) \
            .set_placement((x + 3, y + 3))

        # Coordinates
        msp.add_text(f"{x:.2f},{y:.2f}", dxfattribs={"height": 2, "layer": "LABELS"}) \
            .set_placement((x + 3, y - 3))

    # ✅ CLOSE SHAPE
    if points[0] != points[-1]:
        points.append(points[0])

    msp.add_lwpolyline(points, dxfattribs={"layer": "LINES"})

    # ✅ AREA FIXED
    area = calculate_area(points)

    # ✅ GRID
    add_grid(msp, points[0][0] - 50, points[0][1] - 50)

    # ✅ TITLE BLOCK FIX
    base_x, base_y = points[0]

    msp.add_text(
        f"Project: {project_name}",
        dxfattribs={
            "height": 5,
            "layer": "META",
            "color": 4,        # cyan
            "lineweight": 50   # thicker text
        }
    ).set_placement((base_x + 80, base_y + 80))


    msp.add_text(
        f"Date: {datetime.now().date()}",
        dxfattribs={
            "height": 5,
            "layer": "META",
            "color": 4,
            "lineweight": 50
        }
    ).set_placement((base_x + 80, base_y + 70))


    msp.add_text(
        f"Area: {area:.2f} sq.units",
        dxfattribs={
            "height": 5,
            "layer": "META",
            "color": 4,
            "lineweight": 50
        }
    ).set_placement((base_x + 80, base_y + 60))


    msp.add_text(
        "Scale: 1:1",
        dxfattribs={
            "height": 5,
            "layer": "META",
            "color": 4,
            "lineweight": 50
        }
    ).set_placement((base_x + 80, base_y + 50))

    # SAVE
    output_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.dxf")
    doc.saveas(output_path)

    return output_path


# -------------------- MULTI FILE API --------------------
@router.post("/upload-multiple")
async def convert_multiple(files: list[UploadFile] = File(...)):
    outputs = []

    for file in files:
        temp_path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4()}.csv")

        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            dxf_path = csv_to_dxf(temp_path, project_name=file.filename)
            outputs.append(dxf_path)
        except:
            continue
        finally:
            os.remove(temp_path)

    if not outputs:
        raise HTTPException(status_code=400, detail="No valid files processed")

    return {"files": outputs}


# -------------------- SINGLE FILE API --------------------
@router.post("/csv-to-dxf")
async def convert(file: UploadFile = File(...)):
    temp_path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4()}.csv")

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        dxf_path = csv_to_dxf(temp_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        os.remove(temp_path)

    return FileResponse(dxf_path, media_type="application/dxf", filename="output.dxf")