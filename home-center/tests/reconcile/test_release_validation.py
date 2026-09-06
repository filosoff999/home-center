from ops.reconcile.validator import validate_release_identity


def test_release_identity_accepts_valid_sha():
    result = validate_release_identity(
        {
            "version": "0.5.0",
            "revision": "1d1ff0be759667c40361bbd04b9da273a778b9c8",
            "artifact_sha256": "3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a",
            "artifact_size": 1,
        }
    )

    assert result is True


def test_release_identity_rejects_missing_revision():
    try:
        validate_release_identity({"version": "0.5.0"})
    except ValueError:
        return

    raise AssertionError("missing revision must be rejected")
