"""Hash-bound recovery from the documented public model-26 MDP3/OTP evidence."""
import hashlib
from pathlib import Path
from .extract import key_recipient_id
from .mdp import recover_amlogic_model_key_field, write_private_key_exclusive

FIELD_SHA256 = "140843cf0a950ab3891498fecb7c658dd996ded54bafeefdfc886b8b39956b5c"
OTP_SHA256 = "5c218954d7ee17fb84d75c937e6fae1f4c7b0ae434f2185992786c8c08208e5b"
RECIPIENT_ID = "b271039ff7ab0f3d395039f1f079ff71af9dea71"


def verified_bytes(path: Path, expected_length: int, expected_sha256: str) -> bytes:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != expected_length or digest != expected_sha256:
        raise ValueError(
            f"{path}: expected {expected_length} bytes and SHA-256 {expected_sha256}; "
            f"got {len(data)} bytes and {digest}"
        )
    return data


def recover(field_path: Path, otp_path: Path, output: Path) -> str:
    field = verified_bytes(field_path, 0x800, FIELD_SHA256)
    otp = verified_bytes(otp_path, 0x100, OTP_SHA256)
    key = recover_amlogic_model_key_field(field, otp)
    recipient = key_recipient_id(key)
    if recipient != RECIPIENT_ID:
        raise ValueError(
            f"unexpected recipient {recipient}; expected model-26 recipient {RECIPIENT_ID}"
        )
    write_private_key_exclusive(output, key)
    return recipient
