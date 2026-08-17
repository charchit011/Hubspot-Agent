"""Salesforce metadata XML emitters.

The ONLY place XML is produced. CLAUDE.md rule 3: never generate metadata XML
in conversation — if the output is wrong, fix the emitter here, so the fix
applies to every future client rather than to one file.

Pure functions: same input, same bytes out. No I/O, no clock, no randomness.
That property is what makes 04 re-runnable and diffable.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

NS = "http://soap.sforce.com/2006/04/metadata"
HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'

# Types carrying a length attribute.
LENGTH_TYPES = {"Text", "TextArea", "LongTextArea", "RichTextArea", "Url", "EncryptedText"}
# Types carrying precision/scale.
NUMERIC_TYPES = {"Number", "Currency", "Percent"}
# Types requiring visibleLines.
VISIBLE_LINES_TYPES = {"LongTextArea", "RichTextArea", "MultiselectPicklist"}


def _esc(value) -> str:
    return escape(str(value)) if value is not None else ""


def _tag(name, value, indent=1) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"{'    ' * indent}<{name}>{_esc(value)}</{name}>\n"


def _wrap(root: str, body: str) -> str:
    return f'{HEADER}<{root} xmlns="{NS}">\n{body}</{root}>\n'


class MetadataError(Exception):
    """Raised on a combination Salesforce would reject. Fail here, not at deploy."""


# ---------------------------------------------------------------------------
# Custom object
# ---------------------------------------------------------------------------

def custom_object(api_name: str, label: str, plural_label: str,
                  name_field_type: str = "Text", name_field_label: str = "Name",
                  autonumber_format: str | None = None,
                  sharing_model: str = "ReadWrite",
                  deployment_status: str = "Deployed",
                  description: str = "") -> str:
    """Emit an object-meta.xml.

    Every custom object needs a Name field defined, or the deploy fails with
    REQUIRED_FIELD_MISSING — an error that does not name the real problem.
    """
    if not api_name.endswith("__c"):
        raise MetadataError(f"Custom object {api_name!r} must end with __c.")
    if len(api_name) > 40:
        raise MetadataError(f"Object API name {api_name!r} is {len(api_name)} chars, max 40.")

    body = ""
    body += _tag("deploymentStatus", deployment_status)
    body += _tag("description", description)
    body += _tag("enableActivities", True)
    body += _tag("enableHistory", True)
    body += _tag("enableReports", True)
    body += _tag("enableSearch", True)
    body += _tag("label", label)

    body += "    <nameField>\n"
    if name_field_type == "AutoNumber":
        if not autonumber_format:
            raise MetadataError(f"{api_name}: AutoNumber name field needs a displayFormat.")
        body += _tag("displayFormat", autonumber_format, 2)
    body += _tag("label", name_field_label, 2)
    body += _tag("type", name_field_type, 2)
    body += "    </nameField>\n"

    body += _tag("pluralLabel", plural_label)
    body += _tag("sharingModel", sharing_model)
    return _wrap("CustomObject", body)


# ---------------------------------------------------------------------------
# Custom field
# ---------------------------------------------------------------------------

def custom_field(api_name: str, label: str, field_type: str,
                 length: int | None = None,
                 precision: int | None = None, scale: int | None = None,
                 required: bool = False, unique: bool = False,
                 external_id: bool = False,
                 default_value=None,
                 picklist_values: list[dict] | None = None,
                 restricted_picklist: bool = True,
                 visible_lines: int | None = None,
                 reference_to: str | None = None,
                 relationship_label: str | None = None,
                 relationship_name: str | None = None,
                 delete_constraint: str = "SetNull",
                 formula: str | None = None,
                 formula_return_type: str | None = None,
                 description: str = "") -> str:
    """Emit a field-meta.xml. Validates the combination before emitting."""
    _validate_field(api_name, field_type, length, external_id, unique,
                    picklist_values, reference_to)

    body = ""
    body += _tag("fullName", api_name)
    if external_id:
        body += _tag("externalId", True)

    if field_type == "Formula":
        body += _tag("formula", formula)
        body += _tag("formulaTreatBlanksAs", "BlankAsZero")
        body += _tag("label", label)
        body += _tag("required", False)
        # A formula field's `type` is its return type.
        body += _tag("type", formula_return_type or "Text")
        if (formula_return_type or "Text") in NUMERIC_TYPES:
            body += _tag("precision", precision or 18)
            body += _tag("scale", scale if scale is not None else 2)
        return _wrap("CustomField", body)

    body += _tag("label", label)

    if field_type in LENGTH_TYPES:
        body += _tag("length", length or 255)
    if field_type in NUMERIC_TYPES:
        body += _tag("precision", precision or 18)
        body += _tag("scale", scale if scale is not None else 2)
    if field_type in VISIBLE_LINES_TYPES:
        body += _tag("visibleLines", visible_lines or 4)

    if field_type in ("Lookup", "MasterDetail"):
        body += _tag("referenceTo", reference_to)
        body += _tag("relationshipLabel", relationship_label or label)
        body += _tag("relationshipName", relationship_name or api_name.replace("__c", ""))
        if field_type == "Lookup":
            body += _tag("deleteConstraint", delete_constraint)

    # A master-detail child is required by definition; setting it is an error.
    if field_type != "MasterDetail":
        body += _tag("required", required)
    if unique and field_type not in ("Lookup", "MasterDetail"):
        body += _tag("unique", True)
    if default_value is not None and field_type == "Checkbox":
        body += _tag("defaultValue", bool(default_value))

    body += _tag("description", description)
    body += _tag("type", field_type)

    if field_type in ("Picklist", "MultiselectPicklist"):
        body += _picklist_block(picklist_values or [], restricted_picklist)

    return _wrap("CustomField", body)


def _validate_field(api_name, field_type, length, external_id, unique,
                    picklist_values, reference_to):
    if len(api_name) > 40:
        raise MetadataError(f"Field API name {api_name!r} is {len(api_name)} chars, max 40.")
    if not api_name.endswith("__c"):
        raise MetadataError(f"Custom field {api_name!r} must end with __c.")
    if "__" in api_name[:-3]:
        raise MetadataError(f"Field {api_name!r} contains a double underscore before the suffix.")
    if api_name.startswith("_") or api_name[:-3].endswith("_"):
        raise MetadataError(f"Field {api_name!r} has a leading or trailing underscore.")
    if not api_name[0].isalpha():
        raise MetadataError(f"Field {api_name!r} must start with a letter.")

    if field_type == "Text" and length and length > 255:
        raise MetadataError(
            f"{api_name}: Text max length is 255 (got {length}). Use LongTextArea."
        )
    if external_id and not unique:
        raise MetadataError(
            f"{api_name}: an External ID must also be unique, or Bulk upsert cannot match."
        )
    if external_id and field_type not in ("Text", "Number", "Email", "AutoNumber"):
        raise MetadataError(f"{api_name}: {field_type} cannot be an External ID.")
    if field_type in ("Lookup", "MasterDetail") and not reference_to:
        raise MetadataError(f"{api_name}: {field_type} needs a referenceTo target.")
    if field_type in ("Picklist", "MultiselectPicklist"):
        _validate_picklist(api_name, picklist_values or [])


def _validate_picklist(api_name, values):
    if not values:
        raise MetadataError(f"{api_name}: picklist has no values.")
    seen = set()
    for v in values:
        full = str(v.get("fullName", ""))
        if len(full) > 255:
            raise MetadataError(
                f"{api_name}: picklist value {full[:40]!r}… is {len(full)} chars, max 255."
            )
        if full in seen:
            raise MetadataError(f"{api_name}: duplicate picklist value {full!r}.")
        seen.add(full)


def _picklist_block(values, restricted) -> str:
    body = "    <valueSet>\n"
    body += _tag("restricted", restricted, 2)
    body += "        <valueSetDefinition>\n"
    body += _tag("sorted", False, 3)
    for v in values:
        body += "            <value>\n"
        body += _tag("fullName", v.get("fullName"), 4)
        body += _tag("default", bool(v.get("default", False)), 4)
        body += _tag("label", v.get("label", v.get("fullName")), 4)
        body += "            </value>\n"
    body += "        </valueSetDefinition>\n"
    body += "    </valueSet>\n"
    return body


# ---------------------------------------------------------------------------
# Record type, business process, validation rule, permission set
# ---------------------------------------------------------------------------

def record_type(api_name: str, label: str, description: str = "",
                active: bool = True, business_process: str | None = None,
                picklist_values: list[dict] | None = None) -> str:
    body = ""
    body += _tag("fullName", api_name)
    body += _tag("active", active)
    if business_process:
        body += _tag("businessProcess", business_process)
    body += _tag("description", description)
    body += _tag("label", label)
    for pv in picklist_values or []:
        body += "    <picklistValues>\n"
        body += _tag("picklist", pv["picklist"], 2)
        for value in pv.get("values", []):
            body += "        <values>\n"
            body += _tag("fullName", value, 3)
            body += _tag("default", False, 3)
            body += "        </values>\n"
        body += "    </picklistValues>\n"
    return _wrap("RecordType", body)


def business_process(api_name: str, label: str, values: list[str],
                     active: bool = True, description: str = "") -> str:
    """Sales/Support process. An Opportunity record type without one fails.

    The Metadata API type is BusinessProcess for BOTH sales and support
    processes — there is no SalesProcess/SupportProcess metadata type, despite
    the Setup UI naming them that way. In source format these live at
    objects/{Object}/businessProcesses/{Name}.businessProcess-meta.xml and the
    manifest member is {Object}.{Name}.
    """
    body = ""
    body += _tag("fullName", api_name)
    body += _tag("description", description)
    body += _tag("isActive", active)
    for value in values:
        body += "    <values>\n"
        body += _tag("fullName", value, 2)
        body += _tag("default", False, 2)
        body += "    </values>\n"
    return _wrap("BusinessProcess", body)


def validation_rule(api_name: str, formula: str, error_message: str,
                    active: bool = False, error_field: str | None = None,
                    description: str = "") -> str:
    """Default active=False. Rules written for future data entry reject
    legitimate historical records — CLAUDE.md rule 8."""
    body = ""
    body += _tag("fullName", api_name)
    body += _tag("active", active)
    body += _tag("description", description)
    body += _tag("errorConditionFormula", formula)
    if error_field:
        body += _tag("errorDisplayField", error_field)
    body += _tag("errorMessage", error_message)
    return _wrap("ValidationRule", body)


def permission_set(api_name: str, label: str, field_permissions: list[dict],
                   object_permissions: list[dict] | None = None,
                   description: str = "") -> str:
    """Fields deployed without FLS are invisible to everyone but System
    Administrator, and the client reports the migration as broken. Generated
    automatically from every row marked Y — not optional."""
    body = ""
    body += _tag("description", description)
    body += _tag("hasActivationRequired", False)
    body += _tag("label", label)

    for op in sorted(object_permissions or [], key=lambda o: o["object"]):
        body += "    <objectPermissions>\n"
        body += _tag("allowCreate", op.get("create", True), 2)
        body += _tag("allowDelete", op.get("delete", False), 2)
        body += _tag("allowEdit", op.get("edit", True), 2)
        body += _tag("allowRead", op.get("read", True), 2)
        body += _tag("modifyAllRecords", False, 2)
        body += _tag("object", op["object"], 2)
        body += _tag("viewAllRecords", False, 2)
        body += "    </objectPermissions>\n"

    for fp in sorted(field_permissions, key=lambda f: f["field"]):
        # A required or master-detail field cannot be granted explicit FLS —
        # Salesforce rejects the permission set outright.
        if fp.get("required") or fp.get("type") == "MasterDetail":
            continue
        body += "    <fieldPermissions>\n"
        body += _tag("editable", fp.get("editable", True), 2)
        body += _tag("field", fp["field"], 2)
        body += _tag("readable", fp.get("readable", True), 2)
        body += "    </fieldPermissions>\n"

    return _wrap("PermissionSet", body)


def package_manifest(members_by_type: dict[str, list[str]], api_version: str = "62.0") -> str:
    """package.xml, for convert/validate and as the basis of destructiveChanges."""
    body = ""
    for type_name in sorted(members_by_type):
        members = members_by_type[type_name]
        if not members:
            continue
        body += "    <types>\n"
        for member in sorted(members):
            body += _tag("members", member, 2)
        body += _tag("name", type_name, 2)
        body += "    </types>\n"
    body += _tag("version", api_version)
    return _wrap("Package", body)
