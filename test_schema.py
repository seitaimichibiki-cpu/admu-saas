import urllib.request, json, yaml
from jsonschema import validate

schema_url = "https://render.com/schema/render.yaml.json"
req = urllib.request.Request(schema_url, headers={'User-Agent': 'Mozilla'})
with urllib.request.urlopen(req) as response:
    schema = json.loads(response.read().decode())

with open('render.yaml', 'r') as f:
    data = yaml.safe_load(f)

try:
    validate(instance=data, schema=schema)
    print("Schema is VALID!")
except Exception as e:
    print("Schema error:", e)

