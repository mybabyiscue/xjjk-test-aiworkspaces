import re
with open('output/interface_test_execution_report.md','r',encoding='utf-8') as f:
    c = f.read()

blocks = re.split(r'### IFC_', c)
print(f'Total IFC blocks found: {len(blocks)-1}')
for b in blocks[1:]:
    lines = b.split('\n')
    hdr = lines[0][:60]
    has_fail = 'FAIL' in b
    status = re.search(r'\"status\":\s*\"(FAIL|PASS)\"', b)
    exp = re.search(r'\"expected_business_code\":\s*\"([^\"]+)\"', b)
    act = re.search(r'\"actual_business_code\":\s*\"([^\"]+)\"', b)
    resp_code = re.search(r'"code":\s*"([A-Z]\d+)"', b)
    failed_asserts = len(re.findall(r'\"passed\":\s*false', b))
    total_asserts = len(re.findall(r'\"passed\":\s*(true|false)', b))
    
    out = f'IFC_{hdr[:60]}'
    if status: out += f' | status={status.group(1)}'
    if exp: out += f' | exp={exp.group(1)}'
    if act: out += f' | act={act.group(1)}'
    if resp_code: out += f' | resp_code={resp_code.group(1)}'
    out += f' | asserts={total_asserts}fail={failed_asserts}'
    if has_fail:
        msg = re.search(r'\"msg\":\s*\"([^\"]{0,80})\"', b)
        if msg: out += f' | msg={msg.group(1)}'
    print(out)
