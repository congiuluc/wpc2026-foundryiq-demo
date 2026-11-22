"""Read non-secret azd settings and optionally load them into a notebook kernel."""

import json
import os
import shutil
import subprocess
from pathlib import Path


AZD_KEYS = {
    "AZURE_ENV_NAME", "AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID",
    "AZURE_RESOURCE_GROUP", "AZURE_LOCATION", "AZURE_DEPLOYMENT_NAME",
    "FOUNDRYIQ_DEMO_SEARCH_ENDPOINT", "FOUNDRYIQ_DEMO_OPENAI_ENDPOINT",
    "FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT", "FOUNDRYIQ_DEMO_GPT52_DEPLOYMENT",
    "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT", "FOUNDRYIQ_DEMO_KB_NAME",
    "FOUNDRYIQ_ACL_KB_NAME", "FOUNDRYIQ_ACL_DFS_ENDPOINT",
    "FOUNDRYIQ_ACL_CONTAINER", "FOUNDRYIQ_ACL_GROUP_TECNICI_ID",
    "FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID", "SHAREPOINT_POLICIES_URL",
}


def read_azd_values(environment_name: str = "", *, root: Path | None = None) -> dict[str, str]:
    """Read the selected or named azd environment without changing CLI selections."""
    executable = shutil.which("azd")
    if not executable:
        raise RuntimeError(
            "Azure Developer CLI non trovata: installa azd oppure usa la configurazione manuale/ARM."
        )
    command = [executable, "env", "get-values", "--output", "json", "--no-prompt"]
    if environment_name:
        command += ["--environment", environment_name]
    try:
        proc = subprocess.run(
            command, cwd=root if root is not None else Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Impossibile leggere l'ambiente azd: controlla CLI e ambiente selezionato.") from exc
    if proc.returncode:
        # CLI output can contain settings; never include it in notebook errors.
        raise RuntimeError(
            "Lettura dell'ambiente azd fallita: verifica 'azd env list' e AZD_ENVIRONMENT_NAME."
        )
    try:
        values = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("L'ambiente azd non ha restituito JSON valido.") from exc
    if not isinstance(values, dict):
        raise RuntimeError("L'ambiente azd non ha restituito un oggetto di configurazione.")
    selected: dict[str, str] = {}
    for key in AZD_KEYS:
        if key in values:
            if not isinstance(values[key], str):
                raise RuntimeError(f"Il valore azd {key} deve essere una stringa.")
            selected[key] = values[key]
    return selected


def load_azd_env(
    environment_name: str = "", *, root: Path | None = None, override: bool = False,
) -> dict[str, str]:
    """Load safe settings into this process; return the effective values, without printing them."""
    values = read_azd_values(environment_name, root=root)
    loaded: dict[str, str] = {}
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = os.environ[key]
    return loaded
