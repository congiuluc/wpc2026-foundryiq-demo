"""Uploads data/acl to the ADLS Gen2 container of the ACL demo and applies POSIX ACLs per folder.

Folder -> Entra groups with read access:
  pubblico/               -> Tecnici, Direzione
  manutenzione-riservata/ -> Tecnici
  direzione-riservata/    -> Direzione

Foundry IQ ingests only named user/group entries (owning user/group and "other" are ignored), so every
grant is a named group entry. The root and the folders also get execute (x) so the path can be traversed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "acl"


def build_acl(group_ids: list[str], *, include_default: bool) -> str:
    entries = ["user::rwx", "group::r-x", "other::---", "mask::r-x"]
    entries += [f"group:{gid}:r-x" for gid in group_ids]
    if include_default:
        entries += [f"default:{e}" for e in entries]
    return ",".join(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dfs-endpoint", required=True, help="https://<account>.dfs.core.windows.net/")
    parser.add_argument("--container", required=True)
    parser.add_argument("--tecnici-group-id", required=True)
    parser.add_argument("--direzione-group-id", required=True)
    args = parser.parse_args()

    folders = {
        "pubblico": [args.tecnici_group_id, args.direzione_group_id],
        "manutenzione-riservata": [args.tecnici_group_id],
        "direzione-riservata": [args.direzione_group_id],
    }

    service = DataLakeServiceClient(args.dfs_endpoint, credential=AzureCliCredential())
    fs = service.get_file_system_client(args.container)

    all_groups = [args.tecnici_group_id, args.direzione_group_id]
    fs.get_directory_client("/").set_access_control(acl=build_acl(all_groups, include_default=False))
    print(f"  / -> {len(all_groups)} gruppi (r-x)")

    for folder, groups in folders.items():
        source = DATA_DIR / folder
        directory = fs.get_directory_client(folder)
        directory.create_directory()
        directory.set_access_control(acl=build_acl(groups, include_default=True))
        files = sorted(p for p in source.iterdir() if p.is_file())
        for path in files:
            file_client = directory.get_file_client(path.name)
            file_client.upload_data(path.read_bytes(), overwrite=True)
            # Explicit ACL on the file too, so re-runs stay consistent even if the folder defaults changed.
            file_client.set_access_control(acl=build_acl(groups, include_default=False))
        print(f"  {folder}/ -> {len(groups)} gruppo/i, {len(files)} file")


if __name__ == "__main__":
    main()
