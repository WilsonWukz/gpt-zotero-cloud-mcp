"""End-to-end synthetic Zotero + OAuth + browser approval tests. No live credentials."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from starlette.testclient import TestClient
from zotero_cloud_mcp.app import create_app
from zotero_cloud_mcp.config import Settings, SCOPE, WRITE_SCOPE
from zotero_cloud_mcp.change_store import ChangeStore
from zotero_cloud_mcp.zotero import DataError

ROOT, SUB, OTHER = 'ROOT0001', 'SUBC0001', 'OTHR0001'
A, B, SHARED, NOTE, PDF, ANNOT, TRASH = 'ITEM0001', 'ITEM0002', 'ITEM0003', 'NOTE0001', 'PDFX0001', 'ANNO0001', 'TRSH0001'
PASSWORD = 'synthetic-owner-passphrase-for-tests-only-0000'


def wrap(data):
    return {'key': data['key'], 'version': data['version'], 'data': deepcopy(data)}


def ref(key, title='Example paper', doi='10.0000/example', collections=None):
    return dict(key=key, version=10, itemType='journalArticle', title=title, DOI=doi,
                date='2020', creators=[{'creatorType':'author','firstName':'Test','lastName':'Author'},
                                       {'creatorType':'editor','firstName':'Ed','lastName':'Editor'}],
                tags=[], collections=collections or [ROOT], relations={}, abstractNote='Abstract')


class FakeLibrary:
    def __init__(self):
        self.version = 10
        self.calls = []
        self.fail_index = None
        self.timeout_write = False
        self.omit_status = False
        self.write_right = True
        self.group = False
        self.group_write = True
        self.collections = {ROOT: dict(key=ROOT,version=10,name='Example Collection',parentCollection=False),
                            SUB: dict(key=SUB,version=10,name='Reading',parentCollection=ROOT),
                            OTHER: dict(key=OTHER,version=10,name='PRIVATE OUTSIDE NAME',parentCollection=False)}
        self.items = {A:ref(A), B:ref(B,collections=[SUB]), SHARED:ref(SHARED,'Shared','10.0000/shared',[ROOT,OTHER]),
                      TRASH:dict(ref(TRASH,'Trashed','10.0000/trash'),deleted=1),
                      NOTE:dict(key=NOTE,version=10,itemType='note',parentItem=B,note='<p>private note text</p>',tags=[]),
                      PDF:dict(key=PDF,version=10,itemType='attachment',parentItem=B,title='PDF',linkMode='imported_file',
                               path='/private/never-expose.pdf',contentType='application/pdf',tags=[]),
                      ANNOT:dict(key=ANNOT,version=10,itemType='annotation',parentItem=PDF,annotationType='highlight',
                                 annotationText='Highlighted words',annotationComment='Comment',annotationPosition='{"pageIndex":0,"rects":[[1,2,3,4]]}',tags=[])}
        self.items[B]['tags'] = [{'tag':'duplicate','type':0}]

    @property
    def writes(self):
        return [x for x in self.calls if x['method'] != 'GET']

    def __call__(self, request):
        assert request.url.host == 'api.zotero.org'
        assert request.headers['Zotero-API-Key'] == 'SYNTHETIC-KEY'
        path, query = request.url.path, request.url.params
        self.calls.append({'method':request.method,'path':path,'headers':dict(request.headers),
                           'params':dict(query),'body':json.loads(request.content) if request.content else None})
        headers = {'Last-Modified-Version': str(self.version)}
        if path == '/keys/current':
            return httpx.Response(200,json={'userID':12345,'access':{'user':{'library':True,'write':self.write_right},'groups':{'54321':{'library':self.group,'write':self.group_write}}}},headers=headers)
        if path == '/itemTypeCreatorTypes':
            return httpx.Response(200,json=[{'creatorType':'author'},{'creatorType':'editor'}])
        if path == '/items/new':
            typ = query['itemType']
            t = {'itemType':typ,'tags':[],'relations':{}}
            if typ == 'note':
                t.update(note='',parentItem='')
            elif typ == 'annotation':
                t.update(annotationType=query.get('annotationType','highlight'),annotationText='',annotationComment='',
                         annotationColor='#ffd400',annotationPosition='',annotationSortIndex='',annotationPageLabel='',parentItem='')
            elif typ in {'journalArticle','conferencePaper','book','webpage','preprint'}:
                t.update(title='',DOI='',date='',creators=[],abstractNote='',url='',publicationTitle='',extra='',collections=[])
            else:
                return httpx.Response(400,text='BAD TEMPLATE')
            return httpx.Response(200,json=t)
        prefix = '/groups/54321/' if self.group else '/users/12345/'
        assert path.startswith(prefix)
        suffix = path[len(prefix):]
        bits = suffix.split('/')
        kind = bits[0]
        storage = self.collections if kind == 'collections' else self.items
        if request.method != 'GET':
            assert len(bits) == 1
            if int(request.headers.get('If-Unmodified-Since-Version','-1')) != self.version:
                return httpx.Response(412,text='conflict')
            if self.timeout_write:
                self.version += 1
                raise httpx.ReadTimeout('simulated possible commit',request=request)
            if request.method == 'DELETE':
                field = 'collectionKey' if kind == 'collections' else 'itemKey'
                for key in query[field].split(','):
                    storage.pop(key,None)
                self.version += 1
                return httpx.Response(204,headers={'Last-Modified-Version':str(self.version)})
            payload = json.loads(request.content)
            assert isinstance(payload,list) and 1<=len(payload)<=50
            self.version += 1
            successful, failed = {}, {}
            for i,d in enumerate(payload):
                key = d['key']
                if (self.fail_index is not None and i==self.fail_index) or (key in storage and d['version']!=storage[key]['version']):
                    failed[str(i)]={'key':key,'code':412,'message':'SYNTHETIC-KEY must never be echoed'}
                else:
                    storage[key] = {**storage.get(key,{}),**d,'version':self.version}
                    successful[str(i)]=wrap(storage[key])
            result = {'successful':successful,'unchanged':{},'failed':failed}
            if self.omit_status:
                result['successful']={}
            return httpx.Response(200,json=result,headers={'Last-Modified-Version':str(self.version)})
        if len(bits)>=2 and bits[1] not in {'trash'}:
            if bits[1] not in storage:
                return httpx.Response(404)
            d=storage[bits[1]]
            if len(bits)==2:
                return httpx.Response(200,json=wrap(d),headers={'Last-Modified-Version':str(d['version'])})
            if bits[2]=='fulltext':
                return httpx.Response(200,json={'content':'pdf text '*100,'indexedPages':2,'totalPages':3},headers=headers)
            if bits[2]=='children':
                rows=[x for x in self.items.values() if x.get('parentItem')==bits[1]]
            else:
                assert bits[2:]==['items','top']
                rows=[x for x in self.items.values() if bits[1] in x.get('collections',[]) and not x.get('parentItem')]
        elif suffix=='items/trash':
            rows=[x for x in self.items.values() if x.get('deleted')]
        elif suffix=='items' and 'format' in query:
            return httpx.Response(200,text='@article{synthetic,title={Example paper}}',headers=headers)
        else:
            rows=list(storage.values())
        if kind=='items' or len(bits)>=3:
            if query.get('includeTrashed')!='1' and suffix!='items/trash':
                rows=[x for x in rows if not x.get('deleted')]
        headers['Total-Results']=str(len(rows))
        start,limit=int(query.get('start','0')),int(query.get('limit','100'))
        return httpx.Response(200,json=[wrap(x) for x in rows[start:start+limit]],headers=headers)


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.up=FakeLibrary()
        self.s=Settings(base_url='https://mcp.example.test',app_secret='synthetic-signing-secret-for-tests-000000000',
                        login_password=PASSWORD,zotero_key='SYNTHETIC-KEY',collection_name='Example Collection',
                        allow_write_key=True,enable_writes=True,enable_content_reads=True,state_db=':memory:')
        self.app=create_app(self.s,httpx.MockTransport(self.up))
        self.c=TestClient(self.app,base_url=self.s.base_url)
        self.c.__enter__()
        self.auth=self.app.state.auth
        self.meta={'redirect_uris':['https://chatgpt.com/connector/oauth/test'], 'token_endpoint_auth_method':'client_secret_post','client_name':'Test'}
        self.cid=self.auth.sign('client',self.meta)
        self.token=self.auth.issue_tokens(self.cid,self.meta,scope=SCOPE+' '+WRITE_SCOPE)['access_token']
        self.readtoken=self.auth.issue_tokens(self.cid,self.meta,scope=SCOPE)['access_token']

    def tearDown(self):
        self.c.__exit__(None,None,None)

    def rpc(self,name,args=None,token=None):
        return self.c.post('/mcp',headers={'Authorization':'Bearer '+(token or self.token),'Accept':'application/json, text/event-stream'},
                           json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args or {}}})

    def value(self,name,args=None):
        r=self.rpc(name,args)
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('error',r.json(),r.text)
        result=r.json()['result']
        self.assertFalse(result['isError'],r.text)
        return result['structuredContent']

    def error(self,name,args,code):
        r=self.rpc(name,args)
        self.assertEqual(r.status_code,200,r.text)
        result=r.json()['result']; self.assertTrue(result['isError'],r.text)
        self.assertEqual(result['structuredContent']['error'],code,r.text)

    def plan(self,*actions):
        return self.value('plan_changes',{'actions':list(actions)})

    def approve(self,p):
        path='/review/'+p['plan_id']
        get=self.c.get(path)
        self.assertEqual(get.status_code,200,get.text)
        self.assertNotIn('Example paper',get.text)
        token=re.search(r'name="proof" value="([^"]+)"',get.text)[1]
        view=self.c.post(path,data={'proof':token,'password':PASSWORD},headers={'Origin':self.s.base_url})
        self.assertEqual(view.status_code,200,view.text)
        token=re.search(r'name="proof" value="([^"]+)"',view.text)[1]
        r=self.c.post(path+'/approve',data={'proof':token,'decision':'approve'},headers={'Origin':self.s.base_url})
        self.assertEqual(r.status_code,200,r.text)

    def apply(self,p):
        return self.value('apply_changes',{'plan_id':p['plan_id'],'digest':p['digest']})

    def execute(self,*actions):
        p=self.plan(*actions); self.approve(p); r=self.apply(p)
        self.assertEqual(r['status'],'applied',r)
        return r

    def test_actual_update_and_idempotent_apply(self):
        p=self.plan({'action':'update_items','item_keys':[A],'fields':{'title':'Changed'}})
        self.assertEqual(self.up.writes,[])
        self.assertEqual(p['preview'][0]['before']['title'],'Example paper')
        self.approve(p); self.assertEqual(self.up.writes,[])
        r=self.apply(p); self.assertEqual(r['status'],'applied',r)
        self.assertEqual(self.up.items[A]['title'],'Changed')
        n=len(self.up.writes); self.assertEqual(self.apply(p)['status'],'applied');self.assertEqual(len(self.up.writes),n)
        self.assertTrue(all('if-unmodified-since-version' in x['headers'] for x in self.up.writes))

    def test_old_read_token_cannot_prepare_or_apply_writes(self):
        r=self.rpc('plan_changes',{'actions':[{'action':'trash_items','item_keys':[A]}]},self.readtoken)
        self.assertEqual(r.status_code,403,r.text)
        self.assertEqual(r.json()['error'],'insufficient_scope')
        self.assertEqual(self.up.writes,[])

    def test_owner_approval_cannot_be_forged_as_tool_argument(self):
        p=self.plan({'action':'trash_items','item_keys':[A]})
        self.error('apply_changes',{'plan_id':p['plan_id'],'digest':p['digest']},'OWNER_APPROVAL_REQUIRED')
        self.error('apply_changes',{'plan_id':p['plan_id'],'digest':p['digest'],'confirmed':True},'INVALID_ARGUMENTS')
        self.assertEqual(self.up.writes,[])

    def test_wrong_digest_and_other_client_rejected(self):
        p=self.plan({'action':'trash_items','item_keys':[A]});self.approve(p)
        self.error('apply_changes',{'plan_id':p['plan_id'],'digest':'f'*64},'PLAN_DIGEST_MISMATCH')
        meta={**self.meta,'client_name':'Another client'};cid=self.auth.sign('client',meta)
        token=self.auth.issue_tokens(cid,meta,scope=SCOPE+' '+WRITE_SCOPE)['access_token']
        r=self.rpc('get_change_plan',{'plan_id':p['plan_id']},token)
        self.assertEqual(r.json()['result']['structuredContent']['error'],'PLAN_NOT_FOUND')
        self.assertEqual(self.up.writes,[])

    def test_review_requires_password_cookie_origin_and_escapes_html(self):
        p=self.plan({'action':'update_items','item_keys':[A],'fields':{'title':'<script>alert(1)</script>'}})
        path='/review/'+p['plan_id'];r=self.c.get(path)
        proof=re.search(r'name="proof" value="([^"]+)"',r.text)[1]
        for origin,password in [('https://attacker.test',PASSWORD),(self.s.base_url,'bad')]:
            r=self.c.post(path,data={'proof':proof,'password':password},headers={'Origin':origin})
            self.assertIn(r.status_code,[401,403])
        r=self.c.post(path,data={'proof':proof,'password':PASSWORD},headers={'Origin':self.s.base_url})
        self.assertIn('&lt;script&gt;',r.text);self.assertNotIn('<script>',r.text)
        self.assertEqual(self.up.writes,[])

    def test_collection_create_rename_move_delete_and_root_protection(self):
        p=self.execute({'action':'create_collection','name':'New collection','parent_key':ROOT})
        key=p['preview'][0]['key'];self.assertIn(key,self.up.collections)
        self.execute({'action':'update_collection','collection_key':key,'name':'Renamed','parent_key':SUB})
        self.assertEqual(self.up.collections[key]['parentCollection'],SUB)
        self.execute({'action':'delete_collection','collection_key':key});self.assertNotIn(key,self.up.collections)
        self.error('plan_changes',{'actions':[{'action':'update_collection','collection_key':ROOT,'name':'No'}]},'ROOT_PROTECTED')
        self.error('plan_changes',{'actions':[{'action':'delete_collection','collection_key':SUB}]},'COLLECTION_NOT_EMPTY')

    def test_collection_cycle_and_outside_parent_rejected(self):
        self.error('plan_changes',{'actions':[{'action':'update_collection','collection_key':SUB,'parent_key':SUB}]},'COLLECTION_CYCLE')
        self.error('plan_changes',{'actions':[{'action':'create_collection','name':'No','parent_key':OTHER}]},'COLLECTION_NOT_IN_SCOPE')

    def test_staged_collection_cycles_rejected(self):
        key='SUBC0002';self.up.collections[key]=dict(key=key,version=10,name='Second',parentCollection=ROOT)
        self.error('plan_changes',{'actions':[{'action':'update_collection','collection_key':SUB,'parent_key':key},
                                            {'action':'update_collection','collection_key':key,'parent_key':SUB}]},'COLLECTION_CYCLE')

    def test_memberships_preserve_external_keys_and_keep_inside_scope(self):
        p=self.execute({'action':'set_memberships','item_keys':[SHARED],'add':[SUB],'remove':[ROOT]})
        self.assertEqual(set(self.up.items[SHARED]['collections']),{SUB,OTHER})
        self.assertNotIn(OTHER,json.dumps(p['preview']))
        self.error('plan_changes',{'actions':[{'action':'set_memberships','item_keys':[A],'remove':[ROOT]}]},'SCOPE_ESCAPE')

    def test_tags_and_creator_roles(self):
        self.execute({'action':'edit_tags','item_keys':[A],'add':['first','second']})
        self.execute({'action':'rename_tag','item_keys':[A],'old_tag':'first','new_tag':'renamed'})
        self.execute({'action':'edit_tags','item_keys':[A],'remove':['second']})
        self.assertEqual([x['tag'] for x in self.up.items[A]['tags']],['renamed'])
        d=self.value('get_item_details',{'item_key':A})['item']
        self.assertEqual(d['creators'][1]['creatorType'],'editor')

    def test_import_json_real_create_and_duplicate_guard(self):
        p=self.execute({'action':'import_items','collection_key':SUB,'items':[{'itemType':'journalArticle','title':'A new record','DOI':'10.0000/new','tags':[{'tag':'import'}]}]})
        key=p['preview'][0]['key']; self.assertEqual(self.up.items[key]['collections'],[SUB])
        self.assertEqual(self.up.items[key]['tags'],[{'tag':'import'}])
        self.error('plan_changes',{'actions':[{'action':'import_items','collection_key':ROOT,'items':[{'itemType':'journalArticle','title':'Example paper'}]}]},'DUPLICATE_REFERENCE')

    def test_invalid_metadata_cannot_override_structure(self):
        for field,value in [('collections',[OTHER]),('parentItem',SHARED),('relations',{}),('deleted',1),('itemType','book'),('unknown','x')]:
            self.error('plan_changes',{'actions':[{'action':'update_items','item_keys':[A],'fields':{field:value}}]},'INVALID_FIELDS')
        self.error('plan_changes',{'actions':[{'action':'update_items','item_keys':[A],'fields':{'creators':[{'creatorType':'attacker','name':'x'}]}}]},'INVALID_CREATORS')

    def test_trash_restore_and_shared_destruction_protected(self):
        self.execute({'action':'trash_items','item_keys':[A]});self.assertEqual(self.up.items[A]['deleted'],1)
        self.assertTrue(any(d['key']==A for d in self.value('list_trashed_items')['items']))
        self.execute({'action':'restore_items','item_keys':[A]});self.assertEqual(self.up.items[A]['deleted'],0)
        self.error('plan_changes',{'actions':[{'action':'trash_items','item_keys':[SHARED]}]},'SHARED_ITEM_PROTECTED')
        self.error('plan_changes',{'actions':[{'action':'update_items','item_keys':[SHARED],'fields':{'title':'x'}},
                                            {'action':'trash_items','item_keys':[SHARED]}]},'SHARED_ITEM_PROTECTED')

    def test_permanent_delete_requires_separate_opt_in_and_trash(self):
        self.error('plan_changes',{'actions':[{'action':'delete_items','item_keys':[TRASH]}]},'PERMANENT_DELETE_DISABLED')
        object.__setattr__(self.s,'allow_permanent_delete',True)
        self.error('plan_changes',{'actions':[{'action':'delete_items','item_keys':[A]}]},'TRASH_FIRST')
        p=self.execute({'action':'delete_items','item_keys':[TRASH]});self.assertNotIn(TRASH,self.up.items)
        self.error('plan_undo',{'plan_id':p['plan_id']},'UNDO_UNAVAILABLE')

    def test_revoking_permanent_delete_flag_after_approval_blocks_execution(self):
        object.__setattr__(self.s,'allow_permanent_delete',True)
        p=self.plan({'action':'delete_items','item_keys':[TRASH]});self.approve(p)
        object.__setattr__(self.s,'allow_permanent_delete',False)
        r=self.apply(p);self.assertEqual(r['status'],'failed');self.assertIn(TRASH,self.up.items)
        self.assertEqual(self.up.writes,[])

    def test_notes_annotations_and_pdf_index_reading(self):
        d=self.value('get_item_details',{'item_key':PDF})['item'];self.assertNotIn('path',d)
        children=self.value('list_item_children',{'item_key':B})['items'];self.assertEqual({x['key'] for x in children},{NOTE,PDF})
        self.assertNotIn('note',next(x for x in children if x['key']==NOTE))
        text=self.value('read_item_content',{'item_key':NOTE});self.assertIn('private note',text['content'])
        text=self.value('read_item_content',{'item_key':PDF,'limit':10});self.assertEqual(text['next_start'],10);self.assertEqual(text['indexed_pages'],2)
        self.execute({'action':'update_note','item_key':NOTE,'text':'<script>not executed</script>'})
        self.assertIn('&lt;script&gt;',self.up.items[NOTE]['note'])
        p=self.execute({'action':'create_note','parent_key':A,'text':'A note'});new=p['preview'][0]['key']
        self.assertEqual(self.up.items[new]['parentItem'],A)
        self.execute({'action':'update_annotation','item_key':ANNOT,'fields':{'annotationComment':'Revised'}})
        self.assertEqual(self.up.items[ANNOT]['annotationComment'],'Revised')
        self.execute({'action':'create_annotation','parent_key':PDF,'fields':{'annotationType':'highlight','annotationText':'quoted','annotationPosition':'{"pageIndex":1,"rects":[[1,2,3,4]]}','annotationSortIndex':'00001|000001|00000'}})

    def test_merge_moves_children_unions_tags_and_trashes_after_transfer(self):
        p=self.execute({'action':'merge_items','primary_key':A,'duplicate_keys':[B]})
        self.assertEqual(self.up.items[NOTE]['parentItem'],A);self.assertEqual(self.up.items[PDF]['parentItem'],A)
        self.assertEqual(self.up.items[ANNOT]['parentItem'],PDF)
        self.assertEqual(self.up.items[B]['deleted'],1)
        self.assertIn(SUB,self.up.items[A]['collections'])
        self.assertIn('duplicate',[x['tag'] for x in self.up.items[A]['tags']])
        self.assertIn('http://zotero.org/users/12345/items/'+B,self.up.items[A]['relations']['dc:replaces'])
        self.assertEqual(len(self.up.writes),2)
        self.assertEqual(self.up.writes[-1]['body'][0]['key'],B)

    def test_merge_failure_never_trashes_before_all_transfers_succeed(self):
        p=self.plan({'action':'merge_items','primary_key':A,'duplicate_keys':[B]});self.approve(p)
        self.up.fail_index=0;r=self.apply(p)
        self.assertEqual(r['status'],'partial',r)
        self.assertFalse(self.up.items[B].get('deleted'))
        self.assertEqual(len(self.up.writes),1)
        self.assertNotIn('SYNTHETIC-KEY',json.dumps(r))

    def test_merge_rejects_conflicting_doi_or_type(self):
        self.up.items[B]['DOI']='10.0000/other'
        self.error('plan_changes',{'actions':[{'action':'merge_items','primary_key':A,'duplicate_keys':[B]}]},'AMBIGUOUS_MERGE')

    def test_concurrent_modification_stops_before_write(self):
        p=self.plan({'action':'update_items','item_keys':[A],'fields':{'title':'x'}});self.approve(p)
        self.up.version+=1; r=self.apply(p)
        self.assertEqual(r['status'],'failed');self.assertEqual(self.up.writes,[])
        self.assertEqual(r['receipts'][0]['error'],'VERSION_CONFLICT')

    def test_network_uncertainty_is_not_retried(self):
        p=self.plan({'action':'update_items','item_keys':[A],'fields':{'title':'x'}});self.approve(p)
        self.up.timeout_write=True;r=self.apply(p);self.assertEqual(r['status'],'uncertain',r)
        n=len(self.up.writes);r=self.apply(p);self.assertEqual(len(self.up.writes),n)

    def test_incomplete_200_write_response_is_not_success(self):
        p=self.plan({'action':'update_items','item_keys':[A],'fields':{'title':'x'}});self.approve(p)
        self.up.omit_status=True;r=self.apply(p)
        self.assertEqual(r['status'],'uncertain',r)

    def test_undo_is_separately_reviewed_and_rejects_later_edits(self):
        r=self.execute({'action':'update_items','item_keys':[A],'fields':{'title':'Changed'}})
        undo=self.value('plan_undo',{'plan_id':r['plan_id']})
        self.assertEqual(self.up.items[A]['title'],'Changed')
        self.approve(undo);result=self.apply(undo);self.assertEqual(result['status'],'applied',result)
        self.assertEqual(self.up.items[A]['title'],'Example paper')
        self.error('plan_undo',{'plan_id':r['plan_id']},'UNDO_CONFLICT')

    def test_duplicate_finding_and_export(self):
        self.assertTrue(any(set(x['item_keys'])=={A,B} for x in self.value('find_duplicates')['groups']))
        exported=self.value('export_references',{'item_keys':[A],'format':'bibtex'})
        self.assertIn('@article',exported['text']);self.assertEqual(self.up.writes,[])

    def test_cancelled_plan_cannot_be_applied(self):
        p=self.plan({'action':'trash_items','item_keys':[A]})
        self.value('cancel_change_plan',{'plan_id':p['plan_id']})
        self.error('apply_changes',{'plan_id':p['plan_id'],'digest':p['digest']},'OWNER_APPROVAL_REQUIRED')
        h=self.value('list_change_history');self.assertEqual(h['plans'][0]['status'],'cancelled')

    def test_annotations_and_scopes_are_honest(self):
        r=self.c.post('/mcp',headers={'Authorization':'Bearer '+self.token,'Accept':'application/json, text/event-stream'},
                      json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        tools={t['name']:t for t in r.json()['result']['tools']}
        self.assertFalse(tools['apply_changes']['annotations']['readOnlyHint'])
        self.assertTrue(tools['apply_changes']['annotations']['destructiveHint'])
        self.assertEqual(tools['apply_changes']['securitySchemes'][0]['scopes'],[WRITE_SCOPE])
        status=self.value('zotero_status');self.assertFalse(status['read_only_operations']);self.assertTrue(status['capabilities']['writes_enabled'])
        self.assertEqual(self.c.get('/.well-known/oauth-authorization-server').json()['scopes_supported'],[SCOPE,WRITE_SCOPE])

    def test_read_content_mode_does_not_expose_write_tools(self):
        settings = replace(self.s, enable_writes=False, state_db='')
        with TestClient(create_app(settings, httpx.MockTransport(self.up)), base_url=settings.base_url) as c:
            headers = {'Authorization': 'Bearer ' + self.readtoken, 'Accept': 'application/json, text/event-stream'}
            result = c.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).json()['result']['tools']
            self.assertIn('read_item_content', [x['name'] for x in result])
            self.assertNotIn('apply_changes', [x['name'] for x in result])
            self.assertTrue(all(x['annotations']['readOnlyHint'] for x in result))
        self.assertEqual(self.up.writes, [])

    def test_target_write_right_must_actually_exist(self):
        self.up.write_right = False
        self.error('plan_changes', {'actions':[{'action':'trash_items','item_keys':[A]}]}, 'WRITE_ACCESS_DENIED')
        self.assertEqual(self.up.writes, [])

    def test_item_outside_scope_cannot_be_read_or_written(self):
        key = 'OTHR1111'; self.up.items[key] = ref(key, 'Outside', '10.0000/out', [OTHER])
        self.error('get_item_details', {'item_key':key}, 'ITEM_NOT_IN_SCOPE')
        self.error('plan_changes', {'actions':[{'action':'update_items','item_keys':[key],'fields':{'title':'x'}}]}, 'ITEM_NOT_IN_SCOPE')

    def test_no_deleting_destination_collection_in_same_plan(self):
        empty = 'EMPT0001'; self.up.collections[empty] = dict(key=empty,version=10,name='Empty',parentCollection=ROOT)
        self.error('plan_changes', {'actions':[{'action':'delete_collection','collection_key':empty},
             {'action':'set_memberships','item_keys':[A],'add':[empty]}]}, 'OVERLAPPING_ACTIONS')

    def test_multiple_import_actions_share_duplicate_detection(self):
        obj = {'itemType':'journalArticle','title':'New same record'}
        actions = [{'action':'import_items','collection_key':ROOT,'items':[obj]}]*2
        self.error('plan_changes', {'actions':actions}, 'DUPLICATE_REFERENCE')

    def test_partial_index_is_not_claimed_as_full_document(self):
        result = self.value('read_item_content', {'item_key':PDF,'limit':20000})
        self.assertTrue(result['retrieval_complete'])
        self.assertFalse(result['index_complete'])
        self.assertFalse(result['complete'])

    def test_merge_undo_restores_memberships_children_and_duplicate(self):
        result = self.execute({'action':'merge_items','primary_key':A,'duplicate_keys':[B]})
        undo = self.value('plan_undo', {'plan_id':result['plan_id']})
        self.approve(undo); self.assertEqual(self.apply(undo)['status'], 'applied')
        self.assertEqual(self.up.items[NOTE]['parentItem'],B)
        self.assertEqual(self.up.items[PDF]['parentItem'],B)
        self.assertFalse(self.up.items[B]['deleted'])
        self.assertEqual(self.up.items[A]['collections'],[ROOT])

    def test_group_write_checks_exact_group_permission(self):
        self.up.group=True
        settings=replace(self.s,library_type='group',library_id='54321')
        with TestClient(create_app(settings,httpx.MockTransport(self.up)),base_url=settings.base_url) as c:
            auth=c.app.state.auth;cid=auth.sign('client',self.meta)
            token=auth.issue_tokens(cid,self.meta,scope=SCOPE+' '+WRITE_SCOPE)['access_token']
            headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'}
            payload={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'plan_changes',
                     'arguments':{'actions':[{'action':'update_items','item_keys':[A],'fields':{'title':'Group edit'}}]}}}
            result=c.post('/mcp',headers=headers,json=payload).json()['result']
            self.assertFalse(result['isError'],result)
            self.up.group_write=False
            result=c.post('/mcp',headers=headers,json=payload).json()['result']
            self.assertEqual(result['structuredContent']['error'],'WRITE_ACCESS_DENIED')
        self.assertEqual(self.up.writes,[])

    def test_oauth_new_write_scope_and_refresh_cannot_escalate_read_token(self):
        meta=self.meta
        r=self.c.post('/oauth/register',json=meta);client=r.json()
        verifier='v'*64
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        params={'client_id':client['client_id'],'response_type':'code','redirect_uri':meta['redirect_uris'][0],
                'scope':SCOPE+' '+WRITE_SCOPE,'state':'s','resource':self.s.resource,'code_challenge':challenge,'code_challenge_method':'S256'}
        page=self.c.get('/oauth/authorize',params=params);self.assertEqual(page.status_code,200,page.text)
        self.assertIn('reviewed write access',page.text)
        rid=re.search(r'name="request_id" value="([^"]+)"',page.text)[1]
        consent=re.search(r'name="consent_token" value="([^"]+)"',page.text)[1]
        self.auth.pending.clear()  # signed consent recovery preserves scope
        r=self.c.post('/oauth/approve',data={'request_id':rid,'consent_token':consent,'decision':'allow','password':PASSWORD},
                      headers={'Origin':self.s.base_url},follow_redirects=False)
        self.assertEqual(r.status_code,303,r.text)
        code=parse_qs(urlsplit(r.headers['location']).query)['code'][0]
        r=self.c.post('/oauth/token',data={'client_id':client['client_id'],'client_secret':client['client_secret'],
                        'grant_type':'authorization_code','code':code,'redirect_uri':meta['redirect_uris'][0],
                        'code_verifier':verifier,'resource':self.s.resource})
        self.assertEqual(r.status_code,200,r.text);self.assertIn(WRITE_SCOPE,r.json()['scope'])
        read_refresh=self.auth.issue_tokens(self.cid,meta,scope=SCOPE)['refresh_token']
        r=self.c.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':read_refresh,'resource':self.s.resource,
                        'client_id':self.cid,'client_secret':self.auth.client_secret(self.cid),'scope':SCOPE+' '+WRITE_SCOPE})
        self.assertEqual(r.status_code,400,r.text)


class StoreTests(unittest.TestCase):
    def test_sqlite_restart_claim_once_and_client_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'state.sqlite3')
            s=ChangeStore(path);p=s.create('one','epoch',{'preview':[],'steps':[]});s.transition(p['id'],'pending','approved');s.close()
            s=ChangeStore(path)
            with self.assertRaises(DataError):s.get(p['id'],'another','epoch')
            _,claimed=s.claim(p['id'],'one','epoch',p['digest']);self.assertTrue(claimed)
            s.close();s=ChangeStore(path)
            with self.assertRaises(DataError):s.claim(p['id'],'one','epoch',p['digest'])
            self.assertEqual(s.get(p['id'])['status'],'applying');s.close()

    def test_symlink_and_public_directory_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'link'; path.symlink_to(Path(temp)/'actual')
            with self.assertRaises(ValueError):ChangeStore(str(path))
            os.chmod(temp,0o755)
            with self.assertRaises(ValueError):ChangeStore(str(Path(temp)/'state.sqlite3'))
            os.chmod(temp,0o700)


if __name__=='__main__':
    unittest.main()
