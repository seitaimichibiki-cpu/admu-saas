import sys
from pydantic import BaseModel
from typing import Optional

class ClinicUpsertReq(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    license_key: Optional[str] = None
    plan_status: Optional[str] = None
    password: str = ""

req = ClinicUpsertReq(id=1, name="整体院導", license_key=None)
print("DUMP:", req.model_dump())
