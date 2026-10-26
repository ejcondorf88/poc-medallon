from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "main.yml"


def _texto() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_triggers_correctos() -> None:
    texto = _texto()

    assert "push:" in texto
    assert "pull_request:" in texto
    assert "workflow_dispatch:" in texto
    for ruta in (
        "'data/bronce/**'",
        "'andinos_etl/**'",
        "'main.py'",
        "'requirements.txt'",
        "'tests/**'",
        "'.github/workflows/**'",
    ):
        assert ruta in texto


def test_jobs_por_capa_usan_needs() -> None:
    texto = _texto()

    assert "  bronce:" in texto
    assert "  plata:" in texto
    assert "  oro:" in texto
    assert "  publish:" in texto
    assert "needs: [test, bronce]" in texto
    assert "needs: [test, bronce, plata]" in texto
    assert "needs: [test, bronce, plata, oro]" in texto


def test_artifacts_y_calidad_estan_separados() -> None:
    texto = _texto()

    assert "andinos-bronce-${{ github.run_id }}" in texto
    assert "andinos-plata-${{ github.run_id }}" in texto
    assert "andinos-model-approved-${{ github.run_id }}" in texto
    assert "actions/download-artifact@v4" in texto
    assert "actions/upload-artifact@v4" in texto
    assert "--stage bronce" in texto
    assert "--stage plata" in texto
    assert "--stage oro" in texto
    assert "--stage verificar" in texto


def test_publicacion_requiere_aprobacion_permisos_y_main() -> None:
    texto = _texto()

    assert "vars.PUBLISH_ENABLED == 'true'" in texto
    assert "github.ref == 'refs/heads/main'" in texto
    assert "needs.oro.outputs.quality_gate == 'APPROVED'" in texto
    assert "environment: sharepoint-production" in texto
    assert "AZURE_CLIENT_SECRET" in texto
    assert "id-token: write" not in texto


def test_pipeline_tiene_static_checks_pruebas_y_concurrency() -> None:
    texto = _texto()

    assert "python -m compileall -q andinos_etl main.py tests" in texto
    assert "python -m pytest -q" in texto
    assert "concurrency:" in texto
    assert "cancel-in-progress: false" in texto
    assert "permissions:\n  contents: read" in texto
