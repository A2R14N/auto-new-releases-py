import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../anr-bridge.js', import.meta.url), 'utf8');
function fixture(count = 20) {
    let rows = Array.from({length:count}, (_,i)=>({uid:`row-${i}`,uri:`spotify:track:${i % 13}`,addedAt:'original',release:{isoString:'2026-09-30'},artists:[]}));
    const calls = [], notifications = [];
    let failMove = false, dropAdds = false;
    const api = {
        async getContents(uri, {offset=0,limit=500}={}) {calls.push('read');return {items:rows.slice(offset,offset+limit),totalLength:rows.length};},
        async move(uri, selected, location) {
            calls.push('move');if(failMove) throw Error('move failed');
            const ids = new Set(selected.map(x=>x.uid));
            const group = rows.filter(x=>ids.has(x.uid));
            rows = rows.filter(x=>!ids.has(x.uid));
            // Match the client's anchor contract, so bare UID strings cannot
            // accidentally pass tests while Spotify ignores them.
            if (location.after) assert.equal(typeof location.after.uid, 'string');
            else assert.equal(location.before, 'start');
            const index = location.after ? rows.findIndex(x=>x.uid===location.after.uid)+1 : 0;
            rows.splice(index,0,...group);
        },
        async remove(uri,selected){calls.push('remove');const ids=new Set(selected.map(x=>x.uid));rows=rows.filter(x=>!ids.has(x.uid));},
        async add(uri,uris,location){calls.push('add');assert.equal(location.after,'end');if(!dropAdds) rows.push(...uris.map((uri,i)=>({uid:`new-${rows.length+i}`,uri,addedAt:'new'})));},
    };
    const context = vm.createContext({console:{log(){},warn(){},error(){}},queueMicrotask(){},setInterval(){return 1;},clearInterval(){},setTimeout(){return 1;},clearTimeout(){},
        Spicetify:{Platform:{PlaylistAPI:api,UserAPI:{},LibraryAPI:{}},React:{},LocalStorage:{get(){return 'false';}},
                  Menu:{Item:class{register(){} deregister(){}}},showNotification(...args){notifications.push(args);}}});
    vm.runInContext(source,context);
    const original=[...rows];
    return {api,handlers:context.__anrBridge.handlers,calls,notifications,context,get rows(){return rows;},set failMove(v){failMove=v;},set dropAdds(v){dropAdds=v;},
        params(wanted=original){return {playlist_id:'test',track_uris:wanted.map(x=>x.uri),track_uids:wanted.map(x=>x.uid),expected_uris:original.map(x=>x.uri),expected_uids:original.map(x=>x.uid)};},original};
}

test('large nearly sorted playlist uses one move and preserves duplicates and dates',async()=>{
    const f=fixture(1201),wanted=[...f.original.slice(-10),...f.original.slice(0,-10)];
    const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
    assert.equal(result.success,true);assert.equal(result.writes,1);
    assert.deepEqual(f.rows.map(x=>x.uid),wanted.map(x=>x.uid));
    assert.equal(f.calls.includes('add'),false);assert.equal(f.calls.includes('remove'),false);
    assert.ok(f.rows.every(x=>x.addedAt==='original'));
});
test('already sorted requires no writes',async()=>{
    const f=fixture();const result=await f.handlers.reorder_playlist_tracks(f.params());
    assert.equal(result.strategy,'unchanged');assert.deepEqual(f.calls,['read']);
});
test('a plan tied with rewrite cost still uses exact UID moves',async()=>{
    const f=fixture(3),wanted=[...f.original].reverse();
    const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
    assert.equal(result.strategy,'move');assert.equal(result.writes,2);
    assert.deepEqual(f.rows.map(x=>x.uid),wanted.map(x=>x.uid));
});
test('missing native move rewrites changed order but leaves unchanged order alone',async()=>{
    const f=fixture(12);delete f.api.move;
    assert.equal((await f.handlers.reorder_playlist_tracks(f.params())).strategy,'unchanged');
    const wanted=[...f.original].reverse();
    const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
    assert.equal(result.success,true);assert.equal(result.strategy,'rewrite');
    assert.deepEqual(f.rows.map(x=>x.uri),wanted.map(x=>x.uri));
});
test('moves after an already-correct prefix use a UID object anchor',async()=>{
    const f=fixture(21),wanted=[f.original[0],f.original[5],f.original[6],...f.original.slice(1,5),...f.original.slice(7)];
    const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
    assert.equal(result.success,true);assert.equal(result.strategy,'move');
    assert.deepEqual(f.rows.map(x=>x.uid),wanted.map(x=>x.uid));
});
test('large reverse sort uses bounded rewrite with no anchor reads',async()=>{
    const f=fixture(301),wanted=[...f.original].reverse();
    const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
    assert.equal(result.success,true);assert.equal(result.strategy,'rewrite');assert.equal(result.writes,8);
    assert.equal(f.calls.filter(x=>x==='read').length,2);
    assert.deepEqual(f.rows.map(x=>x.uri),wanted.map(x=>x.uri));
});
test('changed snapshot, duplicate UIDs and missing rows are rejected without writes',async()=>{
    for(const mutate of [p=>p.expected_uids[0]='changed',p=>p.track_uids[0]=p.track_uids[1],p=>p.track_uris.pop()]){
        const f=fixture(),p=f.params();mutate(p);const result=await f.handlers.reorder_playlist_tracks(p);
        assert.equal(result.success,false);assert.deepEqual(f.calls,['read']);
    }
});
test('failed native move never falls back to a destructive rewrite',async()=>{
    const f=fixture();f.failMove=true;
    const result=await f.handlers.reorder_playlist_tracks(f.params([...f.original.slice(-1),...f.original.slice(0,-1)]));
    assert.equal(result.success,false);assert.equal(f.calls.includes('remove'),false);
});
test('silent write failures are detected and empty replacement actually clears',async()=>{
    const f=fixture();f.dropAdds=true;
    assert.equal((await f.handlers.replace_playlist_tracks({playlist_id:'test',track_uris:['spotify:track:new']})).success,false);
    const empty=fixture();assert.equal((await empty.handlers.replace_playlist_tracks({playlist_id:'test',track_uris:[]})).success,true);
    assert.equal(empty.rows.length,0);
});
test('notifications escape text and clamp duration',async()=>{
    const f=fixture();await f.handlers.show_notification({message:'Muzică <b> & "test"',is_error:true,duration_ms:99999});
    assert.deepEqual(f.notifications[0],['Muzică &lt;b&gt; &amp; &quot;test&quot;',true,30000]);
    assert.equal((await f.handlers.show_notification({message:''})).success,false);
});
test('playlist reads carry row IDs and release dates',async()=>{
    const f=fixture(1201);const tracks=await f.handlers.get_playlist_tracks({playlist_id:'test'});
    assert.equal(tracks.length,1201);assert.equal(tracks[0].uid,'row-0');assert.equal(tracks[0].track.album.release_date,'2026-09-30');
});
test('random permutations produce exact saved order',async()=>{
    let seed=42;
    for(let attempt=0;attempt<20;attempt++){
        const f=fixture(37),wanted=[...f.original];
        for(let i=wanted.length-1;i>0;i--){seed=(seed*1664525+1013904223)>>>0;const j=seed%(i+1);[wanted[i],wanted[j]]=[wanted[j],wanted[i]];}
        const result=await f.handlers.reorder_playlist_tracks(f.params(wanted));
        assert.equal(result.success,true);assert.deepEqual(f.rows.map(x=>x.uri),wanted.map(x=>x.uri));
    }
});
