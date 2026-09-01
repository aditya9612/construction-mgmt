import io
import qrcode

__all__ = ["generate_qr"]

SUPPORTED_ENTITY_TYPES = ["EQP", "MAT", "LAB", "PRJ", "AST", "VEN"]

DEFAULT_BOX_SIZE = 10
DEFAULT_BORDER = 4
DEFAULT_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_L

def generate_qr(
    entity_type: str,
    entity_id: int,
    box_size: int = DEFAULT_BOX_SIZE,
    border: int = DEFAULT_BORDER,
    error_correction: int = DEFAULT_ERROR_CORRECTION,
) -> io.BytesIO:
    """
    Generates a compact, stateless QR code for a given entity.
    
    This is a generic, reusable utility that generates a QR code entirely in-memory.
    It performs no disk writes, no database interaction, and is framework-independent.
    The generated payload is extremely compact (e.g., 'EQP:25') with no metadata,
    JSON, or Base64 encoding.

    Args:
        entity_type (str): A brief string representing the entity. Must be one of 
            SUPPORTED_ENTITY_TYPES.
        entity_id (int): The unique positive integer ID of the entity.
        box_size (int, optional): Size of the QR code boxes. Defaults to DEFAULT_BOX_SIZE.
        border (int, optional): Size of the QR code border. Defaults to DEFAULT_BORDER.
        error_correction (int, optional): QR code error correction level. Defaults to 
            DEFAULT_ERROR_CORRECTION.

    Returns:
        io.BytesIO: An in-memory buffer containing the PNG image data.

    Raises:
        ValueError: If entity_type is unsupported or empty, or if entity_id is not positive.
        TypeError: If inputs are not of the expected types.
    """
    if not isinstance(entity_type, str):
        raise TypeError(f"entity_type must be a string, got {type(entity_type).__name__}")
    if not isinstance(entity_id, int):
        raise TypeError(f"entity_id must be an integer, got {type(entity_id).__name__}")

    type_prefix = entity_type.strip().upper()
    if not type_prefix:
        raise ValueError("entity_type cannot be empty")
    
    if type_prefix not in SUPPORTED_ENTITY_TYPES:
        raise ValueError(f"Unsupported entity type: '{type_prefix}'. Must be one of {SUPPORTED_ENTITY_TYPES}")
        
    if entity_id <= 0:
        raise ValueError("entity_id must be a positive integer")

    # Generate compact payload (e.g., EQP:25)
    payload = f"{type_prefix}:{entity_id}"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=error_correction,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Save to in-memory buffer
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)

    return buf
