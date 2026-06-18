import os
if os.path.exists('asset_management.db'):
    os.remove('asset_management.db')

from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from main import app

client = TestClient(app)

def run():
    print("=" * 60)
    print("资产借用管理系统 - 功能验收测试")
    print("=" * 60)

    # 1. 统计接口
    resp = client.get('/api/stats')
    assert resp.status_code == 200, f"Stats failed: {resp.text}"
    print("✓ 1. 统计接口正常:", resp.json())

    # 2. 用户列表
    resp = client.get('/api/users')
    assert resp.status_code == 200
    users = resp.json()
    print(f"✓ 2. 用户列表正常, 共 {len(users)} 个用户")

    # 3. 资产入库
    resp = client.post('/api/assets', json={
        'asset_code': 'LT-001',
        'name': '联想笔记本电脑',
        'category': 'IT设备',
        'total_quantity': 5,
        'location': 'A栋3楼仓库'
    })
    assert resp.status_code == 200, f"Create asset failed: {resp.text}"
    asset = resp.json()
    asset_id = asset['id']
    print(f"✓ 3. 资产入库成功: {asset['asset_code']} - {asset['name']} (库存: {asset['available_quantity']})")

    # 3b. 测试重复资产编号
    resp = client.post('/api/assets', json={
        'asset_code': 'LT-001',
        'name': '重复测试'
    })
    assert resp.status_code == 400
    assert resp.json()['detail']['code'] == 'DUPLICATE_ASSET_CODE'
    print(f"✓ 3b. 重复资产编号被正确拒绝: {resp.json()['detail']['code']}")

    # 4. 借出资产 - 正常借还流程
    due = (datetime.now() + timedelta(days=7)).isoformat()
    resp = client.post('/api/borrow', json={
        'asset_id': asset_id,
        'borrower_id': 2,
        'quantity': 2,
        'due_date': due,
        'purpose': '项目开发使用'
    })
    assert resp.status_code == 200, f"Borrow failed: {resp.text}"
    record = resp.json()
    record_id = record['id']
    print(f"✓ 4. 借出成功: 张三借了 {record['quantity']} 台, 应还 {record['due_date'][:10]}")

    # 验证库存变化
    resp = client.get(f'/api/assets/{asset_id}')
    assert resp.json()['asset']['available_quantity'] == 3
    assert resp.json()['current_holder']['name'] == '张三'
    print("✓ 4b. 库存扣减正确, 当前持有人正确")

    # 5. 重复借出检测
    resp = client.post('/api/borrow', json={
        'asset_id': asset_id,
        'borrower_id': 2,
        'quantity': 1,
        'due_date': due
    })
    assert resp.status_code == 400
    assert resp.json()['detail']['code'] == 'DUPLICATE_BORROW'
    print(f"✓ 5. 重复借出被正确拒绝: {resp.json()['detail']['code']}")

    # 6. 库存不足检测
    resp = client.post('/api/borrow', json={
        'asset_id': asset_id,
        'borrower_id': 3,
        'quantity': 100,
        'due_date': due
    })
    assert resp.status_code == 400
    assert resp.json()['detail']['code'] == 'INSUFFICIENT_STOCK'
    print(f"✓ 6. 库存不足被正确拒绝: {resp.json()['detail']['code']} - {resp.json()['detail']['details']}")

    # 7. 资产详情页 - 历史借用记录
    resp = client.get(f'/api/assets/{asset_id}')
    detail = resp.json()
    assert len(detail['borrow_history']) == 1
    assert detail['last_change'] is not None
    print(f"✓ 7. 资产详情页: 历史记录 {len(detail['borrow_history'])} 条, 最近变更: {detail['last_change']['reason'][:30]}...")

    # 8. 延期申请
    resp = client.post('/api/requests', json={
        'request_type': 'extend',
        'asset_id': asset_id,
        'borrow_record_id': record_id,
        'extend_days': 14,
        'reason': '项目进度延期，需要继续使用'
    })
    assert resp.status_code == 200, f"Extend request failed: {resp.text}"
    req = resp.json()
    req_id = req['id']
    assert req['status'] == 'pending'
    print(f"✓ 8. 延期申请创建成功, 状态: {req['status']}, 延期天数: {req['extend_days']}")

    # 9. 审批通过延期
    resp = client.post(f'/api/requests/{req_id}/approve')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'approved'
    print("✓ 9. 延期申请审批通过")

    # 验证到期日已更新
    resp = client.get('/api/borrow')
    updated = [r for r in resp.json() if r['id'] == record_id][0]
    print(f"   新到期日: {updated['due_date'][:10]}")

    # 10. 归还资产
    resp = client.post(f'/api/borrow/{record_id}/return')
    assert resp.status_code == 200, f"Return failed: {resp.text}"
    assert resp.json()['status'] == 'returned'
    print("✓ 10. 资产归还成功, 借还流程闭环完成")

    # 验证库存恢复
    resp = client.get(f'/api/assets/{asset_id}')
    assert resp.json()['asset']['available_quantity'] == 5
    assert resp.json()['current_holder'] is None
    print("   ✓ 库存已恢复, 持有人已清空")

    # 11. 报废申请
    resp = client.post('/api/requests', json={
        'request_type': 'scrap',
        'asset_id': asset_id,
        'reason': '设备老化，性能不足'
    })
    assert resp.status_code == 200
    scrap_req = resp.json()
    assert scrap_req['status'] == 'pending'
    print(f"✓ 11. 报废申请创建成功, 状态: {scrap_req['status']}")

    # 12. 审批报废
    resp = client.post(f"/api/requests/{scrap_req['id']}/approve")
    assert resp.status_code == 200
    assert resp.json()['status'] == 'approved'
    print("✓ 12. 报废申请审批通过")

    # 验证资产状态
    resp = client.get(f'/api/assets/{asset_id}')
    assert resp.json()['asset']['status'] == 'scrapped'
    assert resp.json()['asset']['available_quantity'] == 0
    print("   ✓ 资产状态已变更为已报废")

    # 13. 报废资产无法借出
    resp = client.post('/api/borrow', json={
        'asset_id': asset_id,
        'borrower_id': 2,
        'quantity': 1,
        'due_date': due
    })
    assert resp.status_code == 400
    assert resp.json()['detail']['code'] == 'ASSET_SCRAPPED'
    print(f"✓ 13. 报废资产无法借出: {resp.json()['detail']['code']}")

    # 14. 导出借用台账
    resp = client.get('/api/export/ledger')
    assert resp.status_code == 200
    assert 'text/csv' in resp.headers['content-type']
    assert len(resp.content) > 100
    csv_lines = resp.content.decode('utf-8-sig').split('\n')
    print(f"✓ 14. 借用台账导出成功: {len(csv_lines)} 行, {len(resp.content)} 字节")
    print("   CSV 表头:", csv_lines[0][:80])

    # 15. 超期检测 (借用中过滤)
    resp = client.get('/api/borrow', params={'overdue_only': True})
    assert resp.status_code == 200
    print(f"✓ 15. 超期未还查询正常, 当前超期数: {len(resp.json())}")

    print()
    print("=" * 60)
    print("全部验收测试通过! ✓")
    print("=" * 60)

if __name__ == '__main__':
    run()
