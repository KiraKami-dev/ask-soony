"""
Adapters turn one classified file into records. One adapter per kind of file.

Each adapter gets the Source and a Context (things learned from other files, such as the
supplier ids) and returns a list of records built with make_record.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ask_soony.verifier.linker.discover import sniff_separator
from ask_soony.verifier.linker.normalize import SupplierResolver


@dataclass
class Context:
    suppliers: SupplierResolver | None


def read_csv(path: Path) -> pd.DataFrame:
    with path.open(encoding="utf-8-sig") as handle:
        separator = sniff_separator(handle.readline())
    return pd.read_csv(path, sep=separator, dtype=str, encoding="utf-8-sig")


def rows(path: Path) -> list[dict]:
    return read_csv(path).to_dict("records")


from ask_soony.verifier.linker.adapters import documents, logistics, master, planning, sap  # noqa: E402

ADAPTERS = {
    "confirmation": documents.confirmation,
    "shipping_advice": documents.shipping_document,
    "packing_list": documents.shipping_document,
    "delivery_note": documents.delivery_note,
    "inspection_report": documents.inspection_report,
    "purchase_orders": sap.purchase_orders,
    "purchase_orders_sap": sap.purchase_orders_sap,
    "goods_receipts": sap.goods_receipts,
    "stock": sap.stock,
    "shipments": logistics.shipments,
    "shipment_tracker": logistics.shipment_tracker,
    "supplier_profile": master.supplier_profile,
    "supplier_profile_json": master.supplier_profile_json,
    "parts_master": master.parts_master,
    "workbook": planning.workbook,
    "products": planning.products,
    "bom": planning.bom,
    "bom_change": planning.bom_change,
    "supplier_parts": planning.supplier_parts,
    "sites": planning.sites,
}

# Run first: their records teach the SupplierResolver every name of every supplier.
PROFILE_KINDS = {"supplier_profile", "supplier_profile_json"}
