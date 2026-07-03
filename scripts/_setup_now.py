import json, urllib.request

D = "http://127.0.0.1:9090"
L = "http://127.0.0.1:14344"
A = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"

# 1. 发现
print("[1] 查询模型...")
req = urllib.request.Request(f"{L}/v1/models", headers={"Authorization": A})
models = [m["id"] for m in json.loads(urllib.request.urlopen(req).read())["data"]]
print(f"    {models}")

# 2. 清理
print("[2] 清理旧实例...")
req = urllib.request.Request(f"{D}/admin/instances")
try:
    for inst in json.loads(urllib.request.urlopen(req).read()):
        iid = inst["instance_id"]
        dr = urllib.request.Request(f"{D}/admin/instances/{iid}", method="DELETE")
        try:
            urllib.request.urlopen(dr)
            print(f"    注销 {iid}")
        except Exception as e:
            print(f"    注销失败 {iid}: {e}")
except Exception as e:
    print(f"    (无旧实例)")

# 3. 注册
print("[3] 注册 Qwen 实例...")
for model in models:
    if "embed" in model.lower():
        continue
    iid = "lm-" + model.replace("/", "-").replace(":", "-")[-25:]
    body = json.dumps({"instance_id": iid, "address": L, "model": model, "engine_type": "vllm"}).encode()
    req = urllib.request.Request(f"{D}/admin/instances", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print(f"    OK  {iid}  ->  model={model}")
        for w in resp.get("warnings", []):
            print(f"        WARN: {w}")
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        print(f"    ERR {iid}: {e.code} {err.get('detail','')}")

# 4. 验证
print("[4] 验证注册表:")
req = urllib.request.Request(f"{D}/admin/instances")
for inst in json.loads(urllib.request.urlopen(req).read()):
    print(f"    {inst['instance_id']:>25s}  {inst['model']:<35s}  {inst['status']}")
