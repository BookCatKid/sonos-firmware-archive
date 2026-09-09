from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from sonos_firmware.legacy import (
    SYSTEM_WORD,
    UPDATER_MARKER,
    WRAPPER_BYTES,
    _legacy_random_material,
    _pkcs12_key,
    _rc4,
    legacy_seed,
    recover_legacy_updater_key,
    write_recovery_evidence_exclusive,
)
from sonos_firmware.mdp import key_recipient_id


def _der(tag: int, value: bytes) -> bytes:
    if len(value) < 0x80:
        length = bytes([len(value)])
    else:
        encoded = len(value).to_bytes((len(value).bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(encoded)]) + encoded
    return bytes([tag]) + length + value


def _synthetic_updater(model: int, private_key: rsa.RSAPrivateKey) -> bytes:
    key, iv, password = _legacy_random_material(model, SYSTEM_WORD)
    salt = bytes.fromhex("0102030405060708")
    iterations = 2048
    private_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    encrypted = _rc4(_pkcs12_key(password, salt, iterations), private_der)
    oid = _der(0x06, bytes.fromhex("2a864886f70d010c0101"))
    parameters = _der(0x30, _der(0x04, salt) + _der(0x02, iterations.to_bytes(2, "big")))
    wrapped = _der(0x30, _der(0x30, oid + parameters) + _der(0x04, encrypted))
    padding = WRAPPER_BYTES - len(wrapped)
    assert 0 < padding <= 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(wrapped + bytes([padding]) * padding) + encryptor.finalize()
    return bytes(37) + UPDATER_MARKER + ciphertext + bytes(19)


def test_model9_derivation_vector():
    key, iv, password = _legacy_random_material(9, SYSTEM_WORD)
    assert legacy_seed(9).hex() == "3230ca989b4a0000ce10e47d00000009436f7079726967687420961900010001"
    assert key.hex() == "b3c8fea138eb958c5b8258bf7f1c39f8"
    assert iv.hex() == "2ec62422288491671f8d8f26bf204a72"
    assert password.hex() == "8ce8fdc46b0156aea374a7f3aec88b86a8fd97bdd522257df2f94c4226177a"


def test_recovers_key_from_complete_synthetic_updater():
    expected = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    recovered = recover_legacy_updater_key(_synthetic_updater(17, expected), 17)
    assert recovered.wrapper_offset == 37 + len(UPDATER_MARKER)
    assert key_recipient_id(recovered.key) == key_recipient_id(expected)


def test_rejects_missing_marker():
    try:
        recover_legacy_updater_key(bytes(4096), 9)
    except ValueError as error:
        assert "marker" in str(error)
    else:
        raise AssertionError("missing marker was accepted")


def test_recovers_key_from_explicit_wrapper_offset():
    expected = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    updater = _synthetic_updater(12, expected)
    wrapper_offset = 37 + len(UPDATER_MARKER)
    recovered = recover_legacy_updater_key(
        updater,
        12,
        system_word=SYSTEM_WORD,
        wrapper_offset=wrapper_offset,
    )
    assert recovered.wrapper_offset == wrapper_offset
    assert key_recipient_id(recovered.key) == key_recipient_id(expected)


def test_writes_secure_recovery_evidence(tmp_path):
    expected = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    recovered = recover_legacy_updater_key(_synthetic_updater(16, expected), 16)
    destination = tmp_path / "evidence"
    write_recovery_evidence_exclusive(destination, recovered)
    assert {path.name for path in destination.iterdir()} == {
        "aes-key.bin",
        "iv.bin",
        "metadata.json",
        "password.bin",
        "seed.bin",
        "wrapper.ct",
        "wrapper.der",
    }
    assert destination.stat().st_mode & 0o077 == 0
    assert all(path.stat().st_mode & 0o077 == 0 for path in destination.iterdir())
