from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List
from datetime import datetime
import io

from app.db.session import get_db_session
from app.models.dummy_quotation import DummyQuotation, DummyQuotationItem, DummyMeasurementDetail
from app.schemas.dummy_quotation import CreateDummyQuotation, UpdateDummyQuotation, DummyQuotationOut
from app.core.dependencies import get_current_active_user
from app.models.user import User, UserRole

router = APIRouter(prefix="/dummy-quotations", tags=["Dummy Quotations"])

# =========================================================
# CALCULATIONS
# =========================================================

def calculate_cubic_feet(length, width, height):
    return (length or 0) * (width or 0) * (height or 0)

def calculate_cubic_meter(cubic_ft):
    return cubic_ft * 0.0283168

def calculate_brass(cubic_ft):
    return cubic_ft / 100

def calculate_dummy_item_measurements(unit: str, length: float, width: float, height: float, rate: float):
    cubic_ft = calculate_cubic_feet(length, width, height)
    cubic_meter = calculate_cubic_meter(cubic_ft)
    brass = calculate_brass(cubic_ft)
    
    unit_lower = (unit or "").lower()
    
    if unit_lower in ["brass"]:
        quantity = brass
        formula = "brass"
    elif unit_lower in ["m3", "cum", "cubic meter"]:
        quantity = cubic_meter
        formula = "cubic_meter"
    elif unit_lower in ["cft", "ft3", "cubic feet"]:
        quantity = cubic_ft
        formula = "cubic_feet"
    else:
        # Default fallback if unknown, just use cubic feet or 0
        quantity = cubic_ft if cubic_ft > 0 else 1
        formula = "custom"
        
    amount = quantity * rate
    
    return {
        "cubic_feet": round(cubic_ft, 2),
        "cubic_meter": round(cubic_meter, 2),
        "brass": round(brass, 2),
        "quantity": round(quantity, 2),
        "amount": round(amount, 2),
        "formula": formula,
    }


def calculate_dummy_totals(subtotal: float, cgst_percent: float, sgst_percent: float, gst_percent: float = 0.0):
    cgst_amount = (subtotal * cgst_percent) / 100
    sgst_amount = (subtotal * sgst_percent) / 100
    # If legacy gst_percent is provided instead
    if gst_percent > 0 and cgst_percent == 0 and sgst_percent == 0:
        cgst_amount = (subtotal * (gst_percent / 2)) / 100
        sgst_amount = (subtotal * (gst_percent / 2)) / 100
        cgst_percent = gst_percent / 2
        sgst_percent = gst_percent / 2
    
    grand_total = subtotal + cgst_amount + sgst_amount
    
    return {
        "cgst_percent": cgst_percent,
        "sgst_percent": sgst_percent,
        "cgst_amount": round(cgst_amount, 2),
        "sgst_amount": round(sgst_amount, 2),
        "grand_total": round(grand_total, 2)
    }

async def generate_dummy_quotation_no(db: AsyncSession):
    year = datetime.now().year
    result = await db.execute(select(func.max(DummyQuotation.id)))
    last_id = result.scalar()
    next_id = (last_id or 0) + 1
    return f"DQT/{year}/{next_id:04d}"

async def get_dummy_quotation_or_404(quotation_id: int, db: AsyncSession, current_user: User):
    result = await db.execute(
        select(DummyQuotation)
        .options(
            selectinload(DummyQuotation.items).selectinload(DummyQuotationItem.measurements)
        )
        .where(DummyQuotation.id == quotation_id)
    )
    quotation = result.scalars().first()
    if not quotation:
        raise HTTPException(status_code=404, detail="Dummy Quotation not found")
        
    if current_user.role == UserRole.CLIENT:
        # Dummy quotes don't really have client_user_id, so a client cannot see any dummy quote
        raise HTTPException(status_code=403, detail="Not authorized to access dummy quotations")
    else:
        if not current_user.is_super_admin:
            if not current_user.company_id or quotation.company_id != current_user.company_id:
                raise HTTPException(status_code=403, detail="Not authorized to access this dummy quotation")
            
    return quotation

# =========================================================
# ENDPOINTS
# =========================================================

@router.post("/preview")
async def preview_dummy_quotation(
    payload: CreateDummyQuotation,
    current_user: User = Depends(get_current_active_user)
):
    # Just calculate without DB
    subtotal = 0.0
    preview_items = []
    
    for item_in in payload.items:
        item_qty = 0.0
        item_amount = 0.0
        measurements_out = []
        
        if item_in.measurements:
            for m in item_in.measurements:
                calc = calculate_dummy_item_measurements(
                    m.unit or "ft", m.length or 0, m.width or 0, m.height or 0, item_in.rate
                )
                item_qty += calc["quantity"]
                item_amount += calc["amount"]
                measurements_out.append({
                    "id": 0,
                    "length": m.length,
                    "width": m.width,
                    "height": m.height,
                    "unit": m.unit,
                    "cubic_feet": calc["cubic_feet"],
                    "cubic_meter": calc["cubic_meter"],
                    "brass": calc["brass"],
                    "quantity": calc["quantity"],
                    "formula_used": calc["formula"]
                })
        else:
            # If no measurements, default to 0.0
            item_qty = 0.0
            item_amount = item_qty * item_in.rate
            
        preview_items.append({
            "id": 0,
            "title": item_in.title,
            "description": item_in.description,
            "unit": item_in.unit,
            "quantity": round(item_qty, 2),
            "rate": item_in.rate,
            "amount": round(item_amount, 2),
            "measurements": measurements_out
        })
        subtotal += item_amount
        
    totals = calculate_dummy_totals(subtotal, payload.cgst_percent, payload.sgst_percent, payload.gst_percent)
    
    return {
        "id": 0,
        "dummy_quotation_no": "PREVIEW",
        "company_id": current_user.company_id,
        "client_name": payload.client_name,
        "mobile_number": payload.mobile_number,
        "email": payload.email,
        "billing_address": payload.billing_address,
        "gst_number": payload.gst_number,
        "subtotal": round(subtotal, 2),
        "gst_percent": payload.gst_percent,
        "cgst_percent": totals["cgst_percent"],
        "sgst_percent": totals["sgst_percent"],
        "cgst_amount": totals["cgst_amount"],
        "sgst_amount": totals["sgst_amount"],
        "grand_total": totals["grand_total"],
        "notes": payload.notes,
        "created_at": datetime.utcnow().isoformat(),
        "items": preview_items
    }

@router.post("/", response_model=DummyQuotationOut, status_code=status.HTTP_201_CREATED)
async def create_dummy_quotation(
    payload: CreateDummyQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    qt_no = await generate_dummy_quotation_no(db)
    
    new_quote = DummyQuotation(
        dummy_quotation_no=qt_no,
        company_id=current_user.company_id,
        client_name=payload.client_name,
        mobile_number=payload.mobile_number,
        email=payload.email,
        billing_address=payload.billing_address,
        gst_number=payload.gst_number,
        gst_percent=payload.gst_percent,
        cgst_percent=payload.cgst_percent,
        sgst_percent=payload.sgst_percent,
        notes=payload.notes,
    )
    
    subtotal = 0.0
    
    db.add(new_quote)
    await db.flush()
    
    for item_in in payload.items:
        db_item = DummyQuotationItem(
            dummy_quotation_id=new_quote.id,
            title=item_in.title,
            description=item_in.description,
            unit=item_in.unit,
            rate=item_in.rate,
        )
        db.add(db_item)
        await db.flush()
        
        item_qty = 0.0
        item_amount = 0.0
        
        if item_in.measurements:
            for m in item_in.measurements:
                calc = calculate_dummy_item_measurements(
                    m.unit or "ft", m.length or 0, m.width or 0, m.height or 0, item_in.rate
                )
                db_measurement = DummyMeasurementDetail(
                    dummy_quotation_item_id=db_item.id,
                    length=m.length,
                    width=m.width,
                    height=m.height,
                    unit=m.unit,
                    cubic_feet=calc["cubic_feet"],
                    cubic_meter=calc["cubic_meter"],
                    brass=calc["brass"],
                    quantity=calc["quantity"],
                    formula_used=calc["formula"]
                )
                db.add(db_measurement)
                item_qty += calc["quantity"]
                item_amount += calc["amount"]
        else:
            item_qty = 0.0
            item_amount = item_qty * item_in.rate
            
        db_item.quantity = round(item_qty, 2)
        db_item.amount = round(item_amount, 2)
        subtotal += db_item.amount
        
    totals = calculate_dummy_totals(subtotal, new_quote.cgst_percent, new_quote.sgst_percent, new_quote.gst_percent)
    
    new_quote.subtotal = round(subtotal, 2)
    new_quote.cgst_percent = totals["cgst_percent"]
    new_quote.sgst_percent = totals["sgst_percent"]
    new_quote.cgst_amount = totals["cgst_amount"]
    new_quote.sgst_amount = totals["sgst_amount"]
    new_quote.grand_total = totals["grand_total"]
    
    await db.commit()
    await db.refresh(new_quote)
    
    return await get_dummy_quotation_or_404(new_quote.id, db, current_user)

@router.get("/{quotation_id}", response_model=DummyQuotationOut)
async def get_dummy_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    return await get_dummy_quotation_or_404(quotation_id, db, current_user)

@router.put("/{quotation_id}", response_model=DummyQuotationOut)
async def update_dummy_quotation(
    quotation_id: int,
    payload: UpdateDummyQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    quotation = await get_dummy_quotation_or_404(quotation_id, db, current_user)
    
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(quotation, key, value)
        
    totals = calculate_dummy_totals(quotation.subtotal, quotation.cgst_percent, quotation.sgst_percent, quotation.gst_percent)
    
    quotation.cgst_percent = totals["cgst_percent"]
    quotation.sgst_percent = totals["sgst_percent"]
    quotation.cgst_amount = totals["cgst_amount"]
    quotation.sgst_amount = totals["sgst_amount"]
    quotation.grand_total = totals["grand_total"]
    
    await db.commit()
    return await get_dummy_quotation_or_404(quotation_id, db, current_user)

@router.delete("/{quotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dummy_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    quotation = await get_dummy_quotation_or_404(quotation_id, db, current_user)
    await db.delete(quotation)
    await db.commit()
    return None

from fastapi.responses import StreamingResponse
from app.utils.dummy_quotation_pdf import generate_dummy_quotation_pdf
from app.models.settings import CompanySettings
from fastapi.encoders import jsonable_encoder

@router.post(
    "/preview/pdf",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/pdf": {}
            }
        }
    },
)
async def preview_dummy_quotation_pdf_endpoint(
    payload: CreateDummyQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    # Get calculated preview data
    preview_data = await preview_dummy_quotation(payload, current_user)
    
    # Get company settings
    result = await db.execute(select(CompanySettings))
    company_settings = result.scalars().first()
    
    # Generate PDF
    pdf_buffer = generate_dummy_quotation_pdf(preview_data, company_settings)
    
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="dummy_quotation_preview.pdf"'}
    )

@router.get("/{quotation_id}/pdf", response_class=StreamingResponse)
async def get_dummy_quotation_pdf(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    quotation = await get_dummy_quotation_or_404(quotation_id, db, current_user)
    
    # Convert ORM object to dict matching DummyQuotationOut schema
    quotation_dict = jsonable_encoder(DummyQuotationOut.model_validate(quotation))
    
    # Get company settings
    result = await db.execute(select(CompanySettings))
    company_settings = result.scalars().first()
    
    pdf_buffer = generate_dummy_quotation_pdf(quotation_dict, company_settings)
    
    safe_filename = quotation.dummy_quotation_no.replace("/", "-").replace("\\", "-")
    
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}.pdf"'}
    )
