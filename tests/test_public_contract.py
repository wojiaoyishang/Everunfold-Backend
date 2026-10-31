from fastapi.testclient import TestClient

from app.core.config import Settings
from app.entrypoints.api import create_app
from app.modules.content.schemas import ErrorResponse


def test_openapi_documents_the_actual_public_error_envelope(test_settings: Settings) -> None:
    application = create_app(test_settings)
    schema = application.openapi()
    error_schema = schema["components"]["schemas"]["ErrorResponse"]
    assert {"success", "code", "msg"} <= set(error_schema["required"])
    assert error_schema["properties"]["success"]["const"] is False
    assert "detail" not in error_schema["properties"]
    for path, operations in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue
        for operation in operations.values():
            for status in ("404", "422", "503", "500"):
                documented = operation["responses"][status]["content"]["application/json"]
                assert documented["schema"]["$ref"] == "#/components/schemas/ErrorResponse"
    with TestClient(application) as client:
        response = client.get("/v1/contents", params={"page": 0})
    assert response.status_code == 422
    actual = ErrorResponse.model_validate(response.json())
    assert actual.success is False
    assert actual.code == 422
