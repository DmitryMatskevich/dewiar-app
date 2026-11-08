"""Saved revision comparison; IDs distinguish moves from edits."""
import difflib


def compare(store, mid, old_revision, new_revision):
    old, new = store.revision(mid, old_revision), store.revision(mid, new_revision)
    before, after = {b['id']: b for b in old['blocks']}, {b['id']: b for b in new['blocks']}
    changes = []
    for bid in dict.fromkeys([*before, *after]):
        a, b = before.get(bid), after.get(bid)
        status = 'added' if a is None else 'removed' if b is None else 'edited' if a != b else 'unchanged'
        moved = a is not None and b is not None and list(before).index(bid) != list(after).index(bid)
        if status != 'unchanged' or moved:
            changes.append({'block_id':bid, 'status':status, 'moved':moved})
    text = '\n'.join(difflib.unified_diff(old['body'].splitlines(), new['body'].splitlines(),
                                        fromfile=f'v{old_revision}', tofile=f'v{new_revision}', lineterm=''))
    from .source_records import listing
    source_before=listing(store,{'id':mid,'revision':old_revision});source_after=listing(store,{'id':mid,'revision':new_revision})
    source_fields=lambda rows:[{k:r.get(k) for k in ('source_id','task_title','sha256')} for r in rows]
    return {'from_revision':old_revision, 'to_revision':new_revision, 'blocks':changes,
            'sources':{'changed':source_before!=source_after,'before':source_fields(source_before),'after':source_fields(source_after)},
            'author_credit':{'changed':old.get('author_credit')!=new.get('author_credit'),'before':old.get('author_credit'),'after':new.get('author_credit')},
            'fields':{k:{'before':old[k],'after':new[k]} for k in ('title','author','kind','site_id','archived') if old[k]!=new[k]},
            'text':text[:200000], 'truncated':len(text)>200000}
