from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Any
from pydantic import BaseModel, ConfigDict, Field


# ================= PURCHASE ORDER SCHEMAS =================

class PurchaseOrderItemCreate(BaseModel):
    material_master_id: Optional[int] = None
    material_id: Optional[int] = None
    material_name: str
    quantity: Decimal
    rate: Decimal
    unit: Optional[str] = None


class PurchaseOrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_order_id: int
    material_master_id: Optional[int]
    material_id: Optional[int]
    material_name: str
    quantity: Decimal
    received_quantity: Decimal
    rate: Decimal
    total_amount: Decimal
    unit: Optional[str]
    status: str


class PurchaseOrderCreate(BaseModel):
    company_id: int
    supplier_id: int
    project_id: Optional[int] = None
    is_company_wide: bool = False
    boq_item_id: Optional[int] = None
    po_number: Optional[str] = None
    items: List[PurchaseOrderItemCreate]


class PurchaseOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: Optional[int]
    po_number: Optional[str]
    is_company_wide: bool
    supplier_id: int
    project_id: Optional[int]
    status: str
    quantity: Optional[Decimal]
    rate: Optional[Decimal]
    total_amount: Optional[Decimal]
    approved_by_id: Optional[int]
    approved_at: Optional[datetime]
    items: List[PurchaseOrderItemOut] = []


# ================= GOODS RECEIPT NOTE (GRN) SCHEMAS =================

class GoodsReceiptNoteItemCreate(BaseModel):
    po_item_id: Optional[int] = None
    material_master_id: int
    material_id: Optional[int] = None
    received_quantity: Decimal
    unit_rate: Decimal
    batch_number: Optional[str] = None
    qc_remarks: Optional[str] = None


class GoodsReceiptNoteItemQC(BaseModel):
    item_id: int
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    qc_remarks: Optional[str] = None


class GoodsReceiptNoteCreate(BaseModel):
    company_id: int
    project_id: int
    supplier_id: int
    purchase_order_id: Optional[int] = None
    challan_number: Optional[str] = None
    challan_date: Optional[date] = None
    vehicle_number: Optional[str] = None
    idempotency_key: Optional[str] = None
    remarks: Optional[str] = None
    items: List[GoodsReceiptNoteItemCreate]


class GoodsReceiptNoteQCPayload(BaseModel):
    qc_items: List[GoodsReceiptNoteItemQC]


class GoodsReceiptNoteItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    grn_id: int
    po_item_id: Optional[int]
    material_master_id: int
    material_id: Optional[int]
    received_quantity: Decimal
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    unit_rate: Decimal
    total_amount: Decimal
    batch_number: Optional[str]
    qc_remarks: Optional[str]


class GoodsReceiptNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    grn_number: str
    company_id: int
    project_id: int
    supplier_id: int
    purchase_order_id: Optional[int]
    challan_number: Optional[str]
    challan_date: Optional[date]
    vehicle_number: Optional[str]
    status: str
    idempotency_key: Optional[str]
    created_by_id: int
    verified_by_id: Optional[int]
    verified_at: Optional[datetime]
    remarks: Optional[str]
    items: List[GoodsReceiptNoteItemOut] = []


# ================= MATERIAL ISSUE (OUTWARD / MIN) SCHEMAS =================

class MaterialIssueCreate(BaseModel):
    company_id: int
    project_id: int
    material_id: int
    quantity: Decimal
    issue_type: str = "SITE_CONSUMPTION"  # SITE_CONSUMPTION, SUBCONTRACTOR_ISSUE, TRANSFER
    issued_to: Optional[str] = None
    contractor_id: Optional[int] = None
    boq_item_id: Optional[int] = None
    idempotency_key: Optional[str] = None
    remarks: Optional[str] = None


class MaterialIssueAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    allocated_quantity: Decimal
    unit_rate: Decimal
    total_amount: Decimal


class MaterialIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_number: str
    company_id: int
    project_id: int
    material_master_id: int
    material_id: int
    quantity: Decimal
    issue_type: str
    issued_to: Optional[str]
    contractor_id: Optional[int]
    boq_item_id: Optional[int]
    idempotency_key: Optional[str]
    status: str
    created_by_id: int
    remarks: Optional[str]
    allocations: List[MaterialIssueAllocationOut] = []


# ================= DAILY STOCK SCHEMAS =================

class DailyStockRecordCreate(BaseModel):
    company_id: int
    project_id: int
    material_id: int
    physical_count: Decimal
    reconciliation_date: Optional[date] = None
    remarks: Optional[str] = None
    idempotency_key: Optional[str] = None


class DailyStockRecountCreate(BaseModel):
    new_physical_count: Decimal
    recount_notes: Optional[str] = None


class DailyStockReviewCreate(BaseModel):
    review_outcome: str  # ACCEPTED_WITHOUT_ADJUSTMENT, UNDER_AUDIT, ADJUST_INVENTORY
    review_notes: Optional[str] = None


class DailyStockRecountHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    previous_count: Decimal
    new_count: Decimal
    variance: Decimal
    recounted_by_id: int
    recount_notes: Optional[str]


class DailyStockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reconciliation_number: str
    company_id: int
    project_id: int
    material_master_id: int
    material_id: int
    reconciliation_date: date
    system_book_stock: Decimal
    physical_count: Decimal
    variance: Decimal
    status: str
    version: int
    review_outcome: Optional[str]
    recorded_by_id: int
    reviewed_by_id: Optional[int]
    reviewed_at: Optional[datetime]
    remarks: Optional[str]
    recounts: List[DailyStockRecountHistoryOut] = []


class DailyStockBulkRowPreview(BaseModel):
    row_number: int
    project_id: int
    material_id: int
    material_name: Optional[str] = None
    physical_count: Decimal
    reconciliation_date: date
    remarks: Optional[str] = None
    is_valid: bool = True
    errors: List[str] = []


class DailyStockBulkPreviewResponse(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    import_token: Optional[str] = None
    preview_rows: List[DailyStockBulkRowPreview] = []
    errors: List[str] = []


class DailyStockBulkCommitRequest(BaseModel):
    import_token: str
    company_id: Optional[int] = None


class DailyStockBulkCommitResponse(BaseModel):
    success: bool
    message: str
    committed_count: int
    reconciliation_numbers: List[str] = []


# ================= BULK OUTWARD SCHEMAS =================

class BulkOutwardRowPreview(BaseModel):
    row_number: int
    project_id: int
    material_id: int
    material_name: Optional[str] = None
    quantity: Decimal
    issue_type: str
    issued_to: Optional[str] = None
    contractor_id: Optional[int] = None
    boq_item_id: Optional[int] = None
    remarks: Optional[str] = None
    is_valid: bool = True
    errors: List[str] = []


class BulkOutwardPreviewResponse(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    import_token: Optional[str] = None
    preview_rows: List[BulkOutwardRowPreview] = []
    errors: List[str] = []


class BulkOutwardCommitRequest(BaseModel):
    import_token: str
    company_id: Optional[int] = None


class BulkOutwardCommitResponse(BaseModel):
    success: bool
    message: str
    committed_count: int
    issue_numbers: List[str] = []


# ================= CANCELLATION SCHEMAS =================

class CancellationOTPRequest(BaseModel):
    company_id: int
    document_type: str  # PO, GRN, MIN, DAILY_STOCK
    document_id: int
    reason: str


class CancellationOTPVerify(BaseModel):
    company_id: int
    request_id: int
    otp: str


class CancellationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    document_type: str
    document_id: int
    document_number: str
    requested_by_id: int
    reason: str
    status: str
    expires_at: datetime
    attempts: int
    verified_by_id: Optional[int]
    verified_at: Optional[datetime]
