import urllib.request
import urllib.error
import urllib.parse
from pydantic import BaseModel
from typing import Optional

class ClinicUpsertReq(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    license_key: Optional[str] = None
    plan_status: Optional[str] = None
    password: str = ""

print(ClinicUpsertReq(id=1, name="HELLO").model_dump())
