from __future__ import annotations

import io
import json
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader, PdfWriter


class FillOptions(BaseModel):
    output_filename: str = "filled-form.pdf"
    return_type: str = "file"


class FillPayload(BaseModel):
    normalized_result: dict[str, Any]
    options: FillOptions = FillOptions()
    field_mapping: dict[str, str] | None = None


app = FastAPI(title="PDF Fill Service")


def _extract_text_map(payload: FillPayload) -> dict[str, str]:
    normalized_result = payload.normalized_result or {}
    field_values = normalized_result.get("field_values")
    if not isinstance(field_values, dict):
        raise HTTPException(status_code=400, detail="payload.normalized_result.field_values 必須是物件")

    field_mapping = payload.field_mapping or {}
    text_map: dict[str, str] = {}
    for business_key, value in field_values.items():
        if not isinstance(value, dict):
            continue
        text_value = value.get("text", "")
        if text_value is None:
            text_value = ""
        pdf_field_name = field_mapping.get(business_key, business_key)
        text_map[pdf_field_name] = str(text_value)
    return text_map


def _build_filled_pdf(pdf_bytes: bytes, text_map: dict[str, str]) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    writer.clone_document_from_reader(reader)

    for page in writer.pages:
        writer.update_page_form_field_values(page, text_map, auto_regenerate=False)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update(
            {
                "/AcroForm": reader.trailer["/Root"]["/AcroForm"],
            }
        )
        try:
            writer._root_object["/AcroForm"].update({"/NeedAppearances": True})
        except Exception:
            pass

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/fill")
async def fill_pdf(
    blank_pdf: UploadFile = File(...),
    payload: str = Form(...),
    output_filename: str = Form("filled-form.pdf"),
) -> Response:
    try:
        payload_obj = FillPayload.model_validate(json.loads(payload))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"payload 不是有效 JSON: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

    if payload_obj.options.output_filename:
        output_filename = payload_obj.options.output_filename

    pdf_bytes = await blank_pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="blank_pdf 為空")

    text_map = _extract_text_map(payload_obj)

    try:
        filled_pdf = _build_filled_pdf(pdf_bytes, text_map)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 填表失敗: {exc}") from exc

    headers = {
        "Content-Disposition": f'attachment; filename="{output_filename}"',
    }
    return Response(content=filled_pdf, media_type="application/pdf", headers=headers)