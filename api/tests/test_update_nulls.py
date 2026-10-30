import pytest


@pytest.mark.parametrize('resource,fields', [
    ('tasks', ['title', 'priority', 'status']),
    ('notes', ['title', 'content', 'tags']),
    ('conversations', ['title', 'persona']),
])
async def test_required_update_fields_reject_null_and_preserve_record(client, auth_headers, resource, fields):
    created = await client.post(f'/api/v1/{resource}', headers=auth_headers, json={'title': 'Original'})
    assert created.status_code == 201
    url = f'/api/v1/{resource}/{created.json()["id"]}'
    for field in fields:
        response = await client.put(url, headers=auth_headers, json={field: None})
        assert response.status_code == 422
    unchanged = await client.put(url, headers=auth_headers, json={})
    assert unchanged.status_code == 200
    assert unchanged.json()['title'] == 'Original'


async def test_nullable_task_fields_can_still_be_cleared(client, auth_headers):
    created = await client.post('/api/v1/tasks', headers=auth_headers, json={
        'title': 'Task', 'description': 'Details', 'due_date': '2026-11-01',
    })
    response = await client.put(f'/api/v1/tasks/{created.json()["id"]}', headers=auth_headers,
                                json={'description': None, 'due_date': None})
    assert response.status_code == 200
    assert response.json()['description'] is None
    assert response.json()['due_date'] is None
