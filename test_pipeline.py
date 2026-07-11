import asyncio, shutil, uuid, os, pathlib, sys

os.environ['BASE_DIR'] = '/root/ebook-translator'
os.environ['PROVIDERS_PATH'] = '/root/ebook-translator/providers.yaml'
os.environ['DB_PATH'] = '/root/ebook-translator/db.sqlite3'
os.environ['REDIS_URL'] = 'redis://:a3e0a2371ea2df1a047e584396902aa7ef65c06366ad185770dd3a1b9362bc4a@127.0.0.1:6379/0'
os.environ['LOG_DIR'] = '/root/ebook-translator/logs'
os.environ['FONT_DIR'] = '/root/ebook-translator/fonts'

sys.path.insert(0, '/root/ebook-translator')
from ebook_translator.db import init_db, create_job, update_job, upsert_chunk, execute, fetchall, fetchone
from ebook_translator.config import settings, STREAM_TRANSLATE, STREAM_REASSEMBLE, GROUP_TRANSLATE
from ebook_translator.queue import connect, ensure_group
from ebook_translator.pdf import extract_profile, build_chunks, write_profile
from ebook_translator.providers import (
    load_provider_keys_async, pick_available, sleep_until_next_available,
    translate_text, mark_cooldown, RateLimited
)

GLOSSARY_EXTRACT_SYS = (
    "You are a glossary extraction assistant. Given a book excerpt, extract all proper nouns, "
    "invented terms, recurring phrases, and domain-specific vocabulary that should be translated "
    "consistently throughout the book. Return ONLY a JSON array of objects, each with keys: "
    "source_term (string), term_type (one of: name, place, invented_term, recurring_phrase). "
    "Include terms that appear 3+ times across the full book. No explanation, no markdown."
)
GLOSSARY_TRANSLATE_SYS = (
    "You are a professional translator. Translate the following glossary terms into {target}. "
    "Return ONLY a JSON array of objects, each with keys: source_term, target_term. "
    "Preserve names phonetically, translate invented terms consistently. No explanation, no markdown."
)

async def run_test():
    cfg = settings()
    await init_db(cfg.db_path)
    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_TRANSLATE, GROUP_TRANSLATE)

    jid = 'final_test'
    job_dir = cfg.jobs_dir / jid
    if job_dir.exists(): shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy('/root/ebook-translator/test_book.pdf', job_dir / 'source.pdf')

    # Create job
    await create_job(cfg.db_path, {'job_id': jid, 'chat_id': 12345, 'target_lang': 'German', 'source_lang': 'English'})
    await update_job(cfg.db_path, jid, status='ingesting')

    # Ingest PDF
    profile = extract_profile(job_dir / 'source.pdf', 'German', 'English')
    write_profile(job_dir, profile)
    chunks = build_chunks(profile)
    print(f'Ingested: {len(chunks)} chunks, {profile["page_count"]} pages, mode={profile["mode"]}')

    for c in chunks:
        await upsert_chunk(cfg.db_path, dict(
            job_id=jid, chunk_id=str(c['chunk_id']), page_num=c['page_id'],
            block_id=','.join(c['block_ids']), source_text=c['text'],
        ))

    # Glossary extraction
    print('Extracting glossary...')
    keys = await load_provider_keys_async(cfg.providers_path)
    full_text = '\n\n'.join(c['text'] for c in chunks)
    batches = [' '.join(full_text.split()[i:i+2000]) for i in range(0, len(full_text.split()), 2000)]
    seen_terms = {}
    for bidx, batch in enumerate(batches):
        for attempt in range(3):
            pk = await pick_available(r, keys)
            if not pk: await sleep_until_next_available(r); continue
            try:
                res = await translate_text(pk, '', batch, system_override=GLOSSARY_EXTRACT_SYS)
                clean = res.strip()
                if clean.startswith('```'): clean = clean.split('\n',1)[1].rsplit('```',1)[0].strip()
                import json
                terms = json.loads(clean)
                for t in terms:
                    s = t['source_term'].strip()
                    if s not in seen_terms: seen_terms[s] = (t.get('term_type','name'), bidx)
                break
            except RateLimited as e:
                await mark_cooldown(r, pk, e.retry_after)
            except Exception as e:
                print(f'  glossary extract retry {attempt}: {e}')
                await asyncio.sleep(2**attempt)

    for src, (ttp, bidx) in seen_terms.items():
        await execute(cfg.db_path, "INSERT OR IGNORE INTO glossary VALUES (?,?,'',?,?)", (jid, src, ttp, bidx))
    print(f'  extracted {len(seen_terms)} terms: {list(seen_terms.keys())}')

    # Translate glossary terms
    print('Translating glossary...')
    rows = await fetchall(cfg.db_path, "SELECT source_term FROM glossary WHERE job_id=? AND target_term=''", (jid,))
    if rows:
        terms_str = '\n'.join(f'- {r["source_term"]}' for r in rows)
        prompt = f'Translate these glossary terms into German:\n{terms_str}'
        system = GLOSSARY_TRANSLATE_SYS.format(target='German')
        for attempt in range(3):
            pk = await pick_available(r, keys)
            if not pk: await sleep_until_next_available(r); continue
            try:
                res = await translate_text(pk, '', prompt, system_override=system)
                clean = res.strip()
                if clean.startswith('```'): clean = clean.split('\n',1)[1].rsplit('```',1)[0].strip()
                import json
                trans = json.loads(clean)
                for t in trans:
                    s, tg = t.get('source_term','').strip(), t.get('target_term','').strip()
                    if s and tg:
                        await execute(cfg.db_path, "UPDATE glossary SET target_term=? WHERE job_id=? AND source_term=?", (tg, jid, s))
                print(f'  translated {len(trans)} terms')
                break
            except RateLimited as e:
                await mark_cooldown(r, pk, e.retry_after)
            except Exception as e:
                print(f'  glossary translate retry {attempt}: {e}')
                await asyncio.sleep(2**attempt)

    terms2 = await fetchall(cfg.db_path, "SELECT source_term, target_term FROM glossary WHERE job_id=? AND target_term!=''", (jid,))
    glossary_list = [(t["source_term"], t["target_term"]) for t in terms2]
    print(f"  glossary: {glossary_list}")

    # Mark job as translating
    await update_job(cfg.db_path, jid, status='translating', total_pages=profile['page_count'], total_chunks=len(chunks), mode=profile['mode'])

    # Push chunks to translate stream
    for c in chunks:
        await r.xadd(STREAM_TRANSLATE, {'job_id': jid, 'chunk_id': str(c['chunk_id']), 'text': c['text']})
    print(f'Pushed {len(chunks)} chunks to translate stream')

    # Now translate all chunks
    print('\nStarting translation...')
    for c in chunks:
        cid = str(c['chunk_id'])
        # Mark translating
        await execute(cfg.db_path, "UPDATE chunks SET status='translating' WHERE job_id=? AND chunk_id=?", (jid, cid))

        attempts = 0
        while True:
            pk = await pick_available(r, keys)
            if not pk:
                await sleep_until_next_available(r)
                continue
            try:
                # Get glossary context
                glos = await fetchall(cfg.db_path, "SELECT source_term, target_term FROM glossary WHERE job_id=? AND target_term!=''", (jid,))
                ctx_lines = []
                for g in glos:
                    if g['source_term'] in c['text']:
                        ctx_lines.append(f'- "{g["source_term"]}" -> "{g["target_term"]}"')
                ctx = '\n'.join(ctx_lines) if ctx_lines else None

                translated = await translate_text(pk, 'German', c['text'], ctx)
                await execute(cfg.db_path, "UPDATE chunks SET translated_text=?, status='done', provider_used=? WHERE job_id=? AND chunk_id=?",
                              (translated, pk.identity, jid, cid))
                print(f'  chunk {cid}: DONE via {pk.name} (attempts={attempts})')
                break
            except RateLimited as e:
                await mark_cooldown(r, pk, e.retry_after)
                print(f'  chunk {cid}: {pk.name} rate-limited ({e.retry_after}s)')
            except Exception as e:
                attempts += 1
                if attempts < 3:
                    print(f'  chunk {cid}: {pk.name} retry {attempts}: {str(e)[:60]}')
                    continue
                await execute(cfg.db_path, "UPDATE chunks SET status='failed' WHERE job_id=? AND chunk_id=?", (jid, cid))
                print(f'  chunk {cid}: FAILED after {attempts} attempts')
                break

    # Report
    done = await fetchall(cfg.db_path, "SELECT chunk_id, status, provider_used, substr(translated_text,1,60) as preview FROM chunks WHERE job_id=? ORDER BY chunk_id", (jid,))
    done_count = sum(1 for d in done if d['status'] == 'done')
    print(f'\n=== RESULTS: {done_count}/{len(done)} chunks done ===')
    for d in done:
        preview = d['preview'] or ''
        print(f'  {d["chunk_id"]}: {d["status"]} via {d["provider_used"]}: {preview}')

    # Push to reassemble stream for the reassemble worker
    await r.xadd(STREAM_REASSEMBLE, {'job_id': jid})
    
    # Verify glossary consistency
    print('\nGlossary consistency check:')
    for g in terms2:
        src, tg = g['source_term'], g['target_term']
        for d in done:
            if d['status'] == 'done' and d['preview']:
                if src in d['preview']:
                    print(f'  TERM IN OUTPUT: {src}')
    
    await r.aclose()

asyncio.run(run_test())
