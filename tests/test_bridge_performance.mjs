import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../anr-bridge.js', import.meta.url), 'utf8');
function fixture({request = async () => ({}), fetch = async () => ({ok: true, status: 204}), enabled = false,
                  definitions = {getAlbum:'album', queryArtistDiscographyAll:'artist'}} = {}) {
    const timers = new Map();
    let nextId = 0, now = 1000, menu;
    const context = vm.createContext({
        console: {log(){}, warn(){}, error(){}, groupCollapsed(){}, groupEnd(){}},
        Date: class extends Date {static now(){return now;}},
        AbortSignal, fetch, URL, document:{baseURI:'https://xpui.app.spotify.com/index.html'},
        queueMicrotask(callback) {const id = ++nextId; timers.set(id, {callback, delay:0});},
        setTimeout(callback, delay) {const id = ++nextId; timers.set(id, {callback, delay}); return id;},
        clearTimeout(id) {timers.delete(id);},
        Spicetify: {
            Platform: {PlaylistAPI:{}, UserAPI:{}, LibraryAPI:{}}, React:{},
            GraphQL: {Request:request, Definitions:definitions},
            LocalStorage: {get(){return String(enabled);}, set(){}},
            Menu: {Item:class {constructor(_name, _state, callback){menu = this; this.callback = callback;} register(){} deregister(){} setState(){}}},
            showNotification(){},
        },
    });
    vm.runInContext(source, context);
    return {
        context, timers, handlers:context.__anrBridge.handlers,
        get menu(){return menu;},
        async tick() {
            assert.equal(timers.size, 1, 'one scheduled poll only');
            const [id, timer] = [...timers][0];
            timers.delete(id); now += timer.delay;
            await timer.callback();
        },
        nextDelay(){return [...timers.values()][0]?.delay;},
    };
}
function albumPage(offset, total = 205) {
    return {data:{albumUnion:{name:'Album', type:'ALBUM', date:{isoString:'2026-10-01'},
        artists:{items:[{uri:'spotify:artist:a', profile:{name:'Artist'}}]}, coverArt:{sources:[]},
        tracksV2:{totalCount:total, items:Array.from({length:Math.max(0, Math.min(100,total-offset))}, (_,i)=>({
            track:{uri:`spotify:track:${offset+i}`, name:`Track ${offset+i}`, trackNumber:offset+i+1,
                discNumber:1, duration:{totalMilliseconds:1234}, artists:{items:[{uri:'spotify:artist:a',profile:{name:'Artist'}}]}},
        }))},
    }}};
}
test('missing artist query uses installed bundle metadata once for concurrent and repeated reads', async () => {
    let assetReads=0, queryReads=0;
    const hash='a'.repeat(64);
    const f=fixture({definitions:{},fetch:async url=>{
        assetReads++;
        assert.equal(String(url),'https://xpui.app.spotify.com/xpui-routes-artist.js');
        return {ok:true,text:async()=>`const d=new x.l("queryArtistDiscographyAll","query","${hash}",null);`};
    },request:async(definition,{uri})=>{
        queryReads++;
        assert.equal(definition.name,'queryArtistDiscographyAll');
        assert.equal(definition.operation,'query');
        assert.equal(definition.sha256Hash,hash);
        assert.equal(definition.value,null);
        return {data:{artistUnion:{discography:{all:{items:[{releases:{items:[{
            uri:'spotify:album:'+uri.split(':')[2],type:'SINGLE',name:'Release',date:{isoString:'2026-10-01'},
        }]}}]}}}}};
    }});
    const batch=await f.handlers.get_artist_albums_batch({artist_ids:['a','b','c','d']});
    assert.ok(Object.values(batch).every(r=>r.error===null&&r.releases.length===1));
    assert.equal((await f.handlers.get_artist_albums({artist_id:'e'})).length,1);
    assert.equal(assetReads,1);
    assert.equal(queryReads,5);
    assert.equal(f.context.Spicetify.GraphQL.Definitions.queryArtistDiscographyAll,undefined);
});

test('available artist definitions keep the native path without reading any bundle', async () => {
    let assetReads=0;
    const f=fixture({fetch:async()=>{assetReads++;throw Error('Unexpected bundle request');},
        request:async definition=>{
            assert.equal(definition,'artist');
            return {data:{artistUnion:{discography:{all:{items:[]}}}}};
        }});
    assert.equal((await f.handlers.get_artist_albums({artist_id:'a'})).length,0);
    assert.equal(assetReads,0);
});

test('failed or incompatible artist bundle reads stay retryable and never pass an undefined query', async () => {
    for(const mode of ['offline','missing','malformed']){
        let reads=0, queryReads=0;
        const f=fixture({definitions:{},request:async()=>{queryReads++;},fetch:async()=>{
            reads++;
            if(mode==='offline')throw Error('Network unavailable');
            if(mode==='missing')return {ok:false};
            return {ok:true,text:async()=>"const d=new x.l('queryArtistDiscographyAll','query','invalid',null);"};
        }});
        for(let i=0;i<2;i++)await assert.rejects(f.handlers.get_artist_albums({artist_id:'a'}));
        assert.equal(reads,2);
        assert.equal(queryReads,0);
    }
});

test('album details reuse the first response and preserve normalized fields', async () => {
    const calls = [];
    const f = fixture({request:async (definition, args) => {calls.push(args); return albumPage(args.offset, 12);}});
    const album = await f.handlers.get_album({album_id:'a'});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].limit, 100);
    assert.equal(album.release_date, '2026-10-01');
    assert.equal(album.total_tracks, 12);
    assert.equal(album.tracks.items.length, 12);
    assert.equal(album.tracks.items[11].track_number, 12);
    assert.equal(album.tracks.items[0].duration_ms, 1234);
    assert.equal(album.tracks.items[0].artists[0].name, 'Artist');
    const tracks = await f.handlers.get_album_tracks({album_id:'a'});
    assert.deepEqual(tracks, album.tracks.items);
});
test('large albums read every page without fetching the first page twice', async () => {
    const offsets = [];
    const f = fixture({request:async (_definition, args) => {offsets.push(args.offset); return albumPage(args.offset);}});
    const album = await f.handlers.get_album({album_id:'a'});
    assert.deepEqual(offsets, [0,100,200]);
    assert.equal(album.tracks.items.length, 205);
    assert.equal(album.tracks.items[204].uri, 'spotify:track:204');
});
test('failed, empty, changed and malformed album pages never return partial success', async () => {
    for (const failure of ['throw','empty','changed','malformed']) {
        const f = fixture({request:async (_definition, args) => {
            if (!args.offset) return albumPage(0);
            if (failure === 'throw') throw Error('429');
            if (failure === 'malformed') return {data:{albumUnion:{}}};
            const page = albumPage(args.offset);
            if (failure === 'empty') page.data.albumUnion.tracksV2.items = [];
            if (failure === 'changed') page.data.albumUnion.tracksV2.totalCount++;
            return page;
        }});
        await assert.rejects(f.handlers.get_album({album_id:'a'}));
        await assert.rejects(f.handlers.get_album_tracks({album_id:'a'}));
    }
});
test('artist batch bounds concurrency to four and isolates failures from empty discographies', async () => {
    let active = 0, maxActive = 0;
    const f = fixture({request:async (_definition, {uri}) => {
        active++; maxActive = Math.max(active, maxActive);
        try {
            await Promise.resolve();
            if (uri.endsWith(':3')) throw Error('429 Too many requests');
            if (uri.endsWith(':4')) return {data:{artistUnion:{discography:{all:{items:[]}}}}};
            return {data:{artistUnion:{discography:{all:{items:[{releases:{items:[{
                uri:'spotify:album:' + uri.split(':')[2], type:'SINGLE', name:'Release', date:{isoString:'2026-10-01'}, tracks:{totalCount:2},
            }]}}]}}}}};
        } finally {active--;}
    }});
    const batch = await f.handlers.get_artist_albums_batch({artist_ids:Array.from({length:9}, (_,i)=>String(i))});
    assert.equal(maxActive, 4);
    assert.equal(Object.keys(batch).length, 9);
    assert.match(batch['spotify:artist:3'].error, /429/);
    assert.equal(batch['spotify:artist:4'].error, null);
    assert.equal(batch['spotify:artist:4'].releases.length, 0);
    assert.equal(batch['spotify:artist:8'].releases[0].release_date, '2026-10-01');
});
test('artist errors and malformed data cannot masquerade as an empty discography', async () => {
    const f = fixture();
    await assert.rejects(f.handlers.get_artist_albums({artist_id:'a'}), /Could not read/);
    const batch = await f.handlers.get_artist_albums_batch({artist_ids:['a']});
    assert.match(batch['spotify:artist:a'].error, /Could not read/);
});
test('older servers fall back to fast active polling and slow idle polling', async () => {
    const requests = [{id:'1',method:'read'}, {id:'2',method:'read'}];
    const posts = [];
    const f = fixture({enabled:true, fetch:async (url, options) => {
        if (url.endsWith('/status')) return {ok:true, json:async()=>({session_token:'test-token'})};
        if (url.includes('?wait_ms=')) return {ok:false,status:404};
        if (url.endsWith('/response')) {posts.push(JSON.parse(options.body)); return {ok:true,status:200};}
        const request = requests.shift();
        return request ? {ok:true,status:200,json:async()=>request} : {ok:true,status:204};
    }});
    f.handlers.read = async () => 'done';
    await f.tick(); assert.equal(f.nextDelay(), 0);
    await f.tick(); assert.equal(f.nextDelay(), 20);
    await f.tick(); assert.equal(f.nextDelay(), 20);
    assert.deepEqual(posts.map(p=>p.id), ['1','2']);
    for (let i = 0; i < 10; i++) await f.tick();
    assert.equal(f.nextDelay(), 500);
    f.context.__anrBridge.dispose(); assert.equal(f.timers.size, 0);
});
test('long polling immediately opens the next bounded server wait after work or idle', async () => {
    const urls = [];
    let first = true;
    const f = fixture({enabled:true,fetch:async url=>{
        urls.push(url);
        if (url.endsWith('/status')) return {ok:true,json:async()=>({session_token:'token'})};
        if (url.endsWith('/response')) return {ok:true};
        if (first) {first=false;return {ok:true,status:200,json:async()=>({id:'1',method:'read'})};}
        return {ok:true,status:204};
    }});
    f.handlers.read = async ()=>'ok';
    await f.tick();assert.equal(f.nextDelay(),0);
    await f.tick();assert.equal(f.nextDelay(),0);
    assert.equal(urls.filter(u=>u.includes('/request?wait_ms=500')).length,2);
    assert.ok(urls.every(url=>url.startsWith('http://127.0.0.1:7421/')));
});
test('in-flight work has no overlapping poll and disposal prevents rescheduling', async () => {
    let finish, signalStarted;
    const started = new Promise(resolve => {signalStarted = resolve;});
    const f = fixture({enabled:true, fetch:async url => {
        if (url.endsWith('/status')) return {ok:true,json:async()=>({session_token:'token'})};
        if (url.endsWith('/response')) return {ok:true};
        return {ok:true,status:200,json:async()=>({id:'1',method:'slow'})};
    }});
    f.handlers.slow = () => new Promise(resolve => {finish = resolve; signalStarted();});
    const pending = f.tick();
    await started;
    assert.ok(finish);
    assert.equal(f.timers.size, 0);
    f.context.__anrBridge.dispose(); finish('done'); await pending;
    assert.equal(f.timers.size, 0);
});
test('disabled/offline bridge stays idle and rejected response reacquires authentication', async () => {
    let fetched = 0;
    const disabled = fixture({fetch:async()=>{fetched++;}});
    await disabled.tick(); assert.equal(fetched,0); assert.equal(disabled.nextDelay(),500);
    const offline = fixture({enabled:true,fetch:async()=>{throw Error('offline');}});
    await offline.tick(); assert.equal(offline.nextDelay(),500);
    let statuses = 0;
    const auth = fixture({enabled:true,fetch:async url=>{
        if (url.endsWith('/status')) {statuses++; return {ok:true,json:async()=>({session_token:'token'})};}
        if (url.endsWith('/response')) return {ok:false,status:401};
        return {ok:true,status:200,json:async()=>({id:'1',method:'read'})};
    }});
    auth.handlers.read = async ()=>'ok';
    await auth.tick(); assert.equal(auth.nextDelay(),500);
    await auth.tick(); assert.equal(statuses,2);
});
