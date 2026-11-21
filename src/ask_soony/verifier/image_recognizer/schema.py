"""
What the model returns for every document: everything on the paper as claims, plus a verdict.

Values are kept as written (strings), with how they were written (printed, handwritten, stamped)
and whether they are crossed out. Interpretation happens later, in the judge.
"""

NULLABLE_STRING = {"type": ["string", "null"]}

# Frozen so the same thing gets the same name in every run; the judge keys on these.
# Quantities: what the document says was ordered, confirmed, shipped, delivered, received.
FIELDS = [
    "quantity_ordered",
    "quantity_confirmed",
    "quantity_shipped",
    "quantity_delivered",
    "quantity_received",
    "quantity_balance",
    "quantity_rejected",
    "unit_price",
    "total_price",
    "lot_size",
    "sample_size",
    "defective",
    "accept_number",
    "reject_number",
    "packages",
    "weight",
    "date_ordered",
    "date_confirmed",
    "date_shipped",
    "date_expected",
    "date_received",
    "revision",
    "lead_time",
    "minimum_order_quantity",
    "other",
]

VALUE = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "enum": FIELDS},
        "field_label": {
            **NULLABLE_STRING,
            "description": "Only when field is 'other': a short snake_case name for it",
        },
        "value": {"type": "string", "description": "Exactly as written on the document"},
        "number": {
            "type": ["number", "null"],
            "description": "The plain number the value means, reading thousands and decimal "
            "separators by the document's own convention (Polish or German '80.000' is 80000); "
            "null if it is not a number",
        },
        "unit": NULLABLE_STRING,
        "written": {"type": "string", "enum": ["printed", "handwritten", "stamped"]},
        "crossed_out": {"type": "boolean"},
        "legible": {
            "type": "string",
            "enum": ["clear", "unclear"],
            "description": "unclear if the characters themselves are hard to read",
        },
    },
    "required": [
        "field",
        "field_label",
        "value",
        "number",
        "unit",
        "written",
        "crossed_out",
        "legible",
    ],
    "additionalProperties": False,
}

LINE = {
    "type": "object",
    "properties": {
        "item": NULLABLE_STRING,
        "material_id": {**NULLABLE_STRING, "description": "The buyer's part number, e.g. CMP-..."},
        "supplier_part_no": NULLABLE_STRING,
        "description": NULLABLE_STRING,
        "values": {"type": "array", "items": VALUE},
    },
    "required": ["item", "material_id", "supplier_part_no", "description", "values"],
    "additionalProperties": False,
}

REFERENCE = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "po_number",
                "delivery_note",
                "air_waybill",
                "container",
                "batch",
                "goods_receipt",
                "invoice",
                "contract",
                "other",
            ],
        },
        "value": {"type": "string"},
    },
    "required": ["type", "value"],
    "additionalProperties": False,
}

STATEMENT = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "enum": [
                "quality",
                "delay",
                "shipment",
                "quantity",
                "price",
                "capacity",
                "approval_status",
                "revision",
                "terms",
                "other",
            ],
        },
        "text_en": {"type": "string", "description": "The statement in English"},
        "quote": {"type": "string", "description": "Original wording, in the document's language"},
    },
    "required": ["topic", "text_en", "quote"],
    "additionalProperties": False,
}

CORRECTION = {
    "type": "object",
    "properties": {
        "what": {"type": "string", "description": "e.g. 'printed quantity crossed out'"},
        "original": NULLABLE_STRING,
        "replacement": NULLABLE_STRING,
        "where": {"type": "string"},
    },
    "required": ["what", "original", "replacement", "where"],
    "additionalProperties": False,
}

DECISION = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "What was decided on, e.g. a lot or batch"},
        "outcome": {
            "type": "string",
            "enum": [
                "pass",
                "fail",
                "conditional",
                "approved",
                "rejected",
                "accepted",
                "pending",
                "other",
            ],
        },
        "decided_by": NULLABLE_STRING,
        "conditions": {**NULLABLE_STRING, "description": "Conditions attached, if any"},
        "quote": {"type": "string", "description": "Original wording"},
    },
    "required": ["subject", "outcome", "decided_by", "conditions", "quote"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": [
                "delivery_note",
                "order_confirmation",
                "shipping_advice",
                "packing_list",
                "inspection_report",
                "contract",
                "price_list",
                "notice",
                "offer",
                "invoice",
                "org_chart",
                "policy",
                "other",
            ],
        },
        "languages": {"type": "array", "items": {"type": "string"}},
        "issuer": NULLABLE_STRING,
        "recipient": NULLABLE_STRING,
        "document_number": NULLABLE_STRING,
        "document_date": {**NULLABLE_STRING, "description": "ISO date YYYY-MM-DD if readable"},
        "references": {"type": "array", "items": REFERENCE},
        "lines": {"type": "array", "items": LINE},
        "statements": {"type": "array", "items": STATEMENT},
        "decisions": {
            "type": "array",
            "items": DECISION,
            "description": "Outcomes stated on the document: inspection results, approvals, "
            "acceptances, rejections",
        },
        "corrections": {
            "type": "array",
            "items": CORRECTION,
            "description": "Every handwritten change, strike-through, overwrite or stamp note",
        },
        "verdict": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "clean",
                        "has_corrections",
                        "has_warnings",
                        "partially_readable",
                        "unreadable",
                    ],
                },
                "summary_en": {"type": "string"},
                "red_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Anything a buyer must not miss: corrections, short or late "
                    "shipments, failed checks, conditions, contradictions inside the document",
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "observations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What is visible on the document that affects its values: "
                    "unclear handwriting, cut-off or covered text, stamps, signatures, damage",
                },
            },
            "required": ["status", "summary_en", "red_flags", "confidence", "observations"],
            "additionalProperties": False,
        },
    },
    "required": [
        "document_type",
        "languages",
        "issuer",
        "recipient",
        "document_number",
        "document_date",
        "references",
        "lines",
        "statements",
        "decisions",
        "corrections",
        "verdict",
    ],
    "additionalProperties": False,
}

OUTPUT_FORMAT = {"type": "json_schema", "schema": VERDICT_SCHEMA}
