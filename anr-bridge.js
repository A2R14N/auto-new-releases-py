// =============================================================================
// ANR Bridge - Spicetify Extension
// Uses ONLY Spotify internal Platform APIs — zero calls to api.spotify.com.
//
// Install: place in %APPDATA%\spicetify\Extensions\
//          spicetify config extensions anr-bridge.js && spicetify apply
// =============================================================================

(function ANRBridge() {
    "use strict";

    // Wait for Spicetify to be ready (docs pattern)
    if (
        !Spicetify?.Platform?.PlaylistAPI ||
        !Spicetify?.Platform?.UserAPI ||
        !Spicetify?.Platform?.LibraryAPI ||
        !Spicetify?.React
    ) {
        setTimeout(ANRBridge, 100);
        return;
    }

    const BRIDGE_PORT = 7421;
    const POLL_INTERVAL_MS = 500;
    const BASE_URL = `http://localhost:${BRIDGE_PORT}`;
    const EXTENSION_NAME = "ANR Bridge";
    const STORAGE_KEY = "anr-bridge:enabled";

    const Platform = Spicetify.Platform;
    console.log(`[${EXTENSION_NAME}] Platform ready — using internal APIs only`);

    // STATE
    let bridgeConnected = false;
    let toastShown = false;
    let bridgeEnabled = Spicetify.LocalStorage.get(STORAGE_KEY) === "true";

    // NORMALIZERS
    function normalizeSearchArtist(hit) {
        if (!hit) return null;
        const d = hit.data ?? hit;
        if (!d?.uri) return null;
        return {
            uri: d.uri,
            id: d.uri.split(":").pop(),
            name: d.profile?.name ?? d.name ?? "",
            followers: { total: d.stats?.followers ?? d.followers?.total ?? d.followers ?? 0 },
            popularity: d.popularity ?? 0,
            genres: d.genres ?? [],
            images: (d.visuals?.avatarImage?.sources ?? d.images ?? []).map(s => ({
                url: typeof s === "string" ? s : s.url,
            })),
        };
    }

    function parseDateFields(d) {
        if (!d) return null;
        const iso = d.isoString || "";
        const y = String(d.year || "").padStart(4, "0");
        const m = String(d.month || 1).padStart(2, "0");
        const day = String(d.day || 1).padStart(2, "0");
        return iso.slice(0, 10) || (y !== "0000" ? `${y}-${m}-${day}` : null);
    }

    // REQUEST HANDLERS
    const handlers = {

        // =====================================================================
        // USER
        // =====================================================================
        async get_current_user() {
            const username = Platform.username;
            const user = await Platform.UserAPI.getUser(username, { catalogue: "", locale: "" });
            return {
                id: user.username ?? username,
                display_name: user.name ?? username,
                uri: user.uri ?? `spotify:user:${username}`,
            };
        },

        // =====================================================================
        // ARTIST
        // =====================================================================
        async search_artists({ query, limit = 10 }) {
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.searchSuggestions,
                    {
                        query,
                        limit,
                        numberOfTopResults: limit,
                        offset: 0,
                        includeAuthors: false,
                    }
                );

                const items = res?.data?.searchV2?.topResultsV2?.itemsV2 || [];
                return items
                    .map(item => item.item?.data ?? item.data ?? item)
                    .filter(d => d?.uri?.startsWith("spotify:artist:"))
                    .map(normalizeSearchArtist)
                    .filter(Boolean);
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] search_artists error:`, e);
                return [];
            }
        },

        async get_artist({ artist_id }) {
            const uri = `spotify:artist:${artist_id}`;
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.queryArtistOverview,
                    { uri, locale: "" }
                );
                const a = res.data.artistUnion;
                if (!a) return { uri, id: artist_id, name: artist_id };

                return {
                    uri,
                    id: artist_id,
                    name: a.profile?.name ?? "",
                    followers: { total: a.stats?.followers ?? 0 },
                    popularity: 0,
                    genres: [],
                    images: a.visuals?.avatarImage?.sources ?? [],
                };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_artist error:`, e);
                return { uri, id: artist_id, name: artist_id };
            }
        },

        async get_multiple_artists({ artist_ids }) {
            if (!artist_ids?.length) return [];
            const results = await Promise.all(
                artist_ids.map(id => handlers.get_artist({ artist_id: id }).catch(() => null))
            );
            return results.filter(Boolean);
        },

        async get_artist_albums({ artist_id, include_groups = "album,single" }) {
            const uri = `spotify:artist:${artist_id}`;
            const groups = new Set(include_groups.toLowerCase().split(",").map(s => s.trim()));

            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.queryArtistDiscographyAll,
                    { uri, offset: 0, limit: 100 }
                );

                const items = res?.data?.artistUnion?.discography?.all?.items || [];

                return items.map(item => {
                    const rel = item.releases?.items?.[0];
                    if (!rel) return null;

                    const type = (rel.type || "").toLowerCase();
                    if (groups.size > 0 && !groups.has(type)) return null;

                    const releaseDate = parseDateFields(rel.date);

                    return {
                        uri: rel.uri,
                        id: rel.uri.split(":").pop(),
                        name: rel.name || "",
                        album_type: type,
                        release_date: releaseDate,
                        total_tracks: rel.tracks?.totalCount || 0,
                        artists: [{ uri, id: artist_id }],
                        images: rel.coverArt?.sources || [],
                    };
                }).filter(Boolean);
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_artist_albums error:`, e);
                return [];
            }
        },

        async get_artist_top_tracks({ artist_id }) {
            const uri = `spotify:artist:${artist_id}`;
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.queryArtistOverview,
                    { uri, locale: "" }
                );
                const items = res?.data?.artistUnion?.discography?.topTracks?.items || [];
                return items.map(i => {
                    const t = i.track;
                    if (!t) return null;
                    return {
                        uri: t.uri,
                        id: t.uri.split(":").pop(),
                        name: t.name,
                        duration_ms: t.duration?.totalMilliseconds || 0,
                        popularity: parseInt(t.playcount || "0", 10),
                    };
                }).filter(Boolean);
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_artist_top_tracks error:`, e);
                return [];
            }
        },

        // =====================================================================
        // ALBUM
        // =====================================================================
        async get_album({ album_id }) {
            const id = (album_id || "").split(":").pop();
            const uri = `spotify:album:${id}`;
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.getAlbum,
                    { uri, locale: "", offset: 0, limit: 50 }
                );

                const album = res?.data?.albumUnion;
                if (!album) return { uri, id };

                const releaseDate = parseDateFields(album.date);

                return {
                    uri,
                    id,
                    name: album.name || "",
                    album_type: (album.type || "album").toLowerCase(),
                    release_date: releaseDate,
                    total_tracks: album.tracksV2?.totalCount || 0,
                    artists: (album.artists?.items || []).map(a => ({
                        name: a.profile?.name || "",
                        uri: a.uri || "",
                    })),
                    images: album.coverArt?.sources || [],
                    tracks: { items: await handlers.get_album_tracks({ album_id: id }) },
                };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_album error:`, e);
                return { uri, id, tracks: { items: [] } };
            }
        },

        async get_album_tracks({ album_id }) {
            const id = (album_id || "").split(":").pop();
            const uri = `spotify:album:${id}`;
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.getAlbum,
                    { uri, locale: "", offset: 0, limit: 100 }
                );

                const items = res?.data?.albumUnion?.tracksV2?.items || [];
                return items.map(item => {
                    const t = item.track;
                    if (!t) return null;
                    return {
                        uri: t.uri,
                        id: t.uri.split(":").pop(),
                        name: t.name || "",
                        duration_ms: t.duration?.totalMilliseconds || 0,
                        track_number: t.trackNumber || 1,
                        disc_number: t.discNumber || 1,
                        artists: (t.artists?.items || []).map(a => ({
                            name: a.profile?.name || "",
                            uri: a.uri || "",
                            id: (a.uri || "").split(":").pop(),
                        })),
                    };
                }).filter(Boolean);
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_album_tracks error:`, e);
                return [];
            }
        },

        async get_albums_batch({ album_ids }) {
            if (!album_ids?.length) return { results: [], had_429: false };

            let had_429 = false;
            const settled = await Promise.allSettled(
                album_ids.map(id => handlers.get_album({ album_id: id }))
            );

            const results = settled
                .map(s => {
                    if (s.status === "fulfilled") return s.value;
                    const msg = String(s.reason?.message ?? s.reason ?? "");
                    if (msg.includes("429") || msg.toLowerCase().includes("too many")) {
                        had_429 = true;
                    }
                    return null;
                })
                .filter(Boolean);

            return { results, had_429 };
        },

        // =================================================================
        // ALBUM RELEASE DATES — two-phase: artist discography + fallback
        // =================================================================
        async get_album_release_dates({ album_ids, playlist_id }) {
            if (!album_ids?.length) return {};

            const albumsNeeded = new Set(album_ids);
            const albumDates = {};

            // Discover artist IDs from the playlist so we can use
            // discography (one call per artist covers many albums).
            const artistIds = new Set();
            if (playlist_id) {
                try {
                    const uri = `spotify:playlist:${playlist_id}`;
                    const contents = await Platform.PlaylistAPI.getContents(uri);
                    for (const t of (contents?.items || [])) {
                        const albumId = t.album?.uri?.split(":").pop();
                        if (albumId && albumsNeeded.has(albumId)) {
                            for (const a of (t.artists || [])) {
                                if (a.uri) artistIds.add(a.uri.split(":").pop());
                            }
                        }
                    }
                } catch (e) {
                    console.warn(`[${EXTENSION_NAME}] Could not scan playlist:`, e);
                }
            }

            // PHASE 1 — artist discography --------------------------------
            if (artistIds.size > 0) {
                console.log(
                    `[${EXTENSION_NAME}] Release dates phase 1: ` +
                    `${artistIds.size} artists for ${albumsNeeded.size} albums`
                );
                const artistArray = [...artistIds];
                const BATCH = 30;
                const PAUSE = 300;

                for (let i = 0; i < artistArray.length; i += BATCH) {
                    const batch = artistArray.slice(i, i + BATCH);
                    await Promise.allSettled(
                        batch.map(id =>
                            Spicetify.GraphQL.Request(
                                Spicetify.GraphQL.Definitions.queryArtistDiscographyAll,
                                { uri: `spotify:artist:${id}`, offset: 0, limit: 300 }
                            ).then(res => {
                                const discItems =
                                    res?.data?.artistUnion?.discography?.all?.items || [];
                                for (const item of discItems) {
                                    const rel = item.releases?.items?.[0];
                                    if (!rel?.uri) continue;
                                    const albumId = rel.uri.split(":").pop();
                                    if (!albumsNeeded.has(albumId) || albumDates[albumId]) continue;
                                    albumDates[albumId] = parseDateFields(rel.date);
                                }
                            }).catch(() => { })
                        )
                    );

                    if (i + BATCH < artistArray.length) {
                        await new Promise(r => setTimeout(r, PAUSE));
                    }
                }
                console.log(
                    `[${EXTENSION_NAME}] Phase 1 done: ` +
                    `${Object.keys(albumDates).length}/${albumsNeeded.size}`
                );
            }

            // PHASE 2 — direct album lookup for remainder -----------------
            const missing = album_ids.filter(id => !albumDates[id]);
            if (missing.length > 0) {
                console.log(
                    `[${EXTENSION_NAME}] Phase 2: ${missing.length} remaining albums`
                );

                for (let i = 0; i < missing.length; i += 50) {
                    const batch = missing.slice(i, i + 50);
                    await Promise.allSettled(
                        batch.map(id =>
                            Spicetify.GraphQL.Request(
                                Spicetify.GraphQL.Definitions.getAlbum,
                                { uri: `spotify:album:${id}`, locale: "", offset: 0, limit: 1 }
                            ).then(res => {
                                const d = res?.data?.albumUnion?.date;
                                if (d) albumDates[id] = parseDateFields(d);
                            }).catch(() => { })
                        )
                    );

                    if (i + 50 < missing.length) {
                        await new Promise(r => setTimeout(r, 500));
                    }
                }
            }

            console.log(
                `[${EXTENSION_NAME}] Release dates done: ` +
                `${Object.keys(albumDates).length}/${albumsNeeded.size}`
            );
            return albumDates;
        },

        // =====================================================================
        // TRACK
        // =====================================================================
        async get_track({ track_id }) {
            const id = (track_id || "").split(":").pop();
            const uri = `spotify:track:${id}`;
            try {
                const res = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.getTrack,
                    { uri }
                );

                const t = res?.data?.trackUnion;
                if (!t) return { uri, id };

                const releaseDate = parseDateFields(t.albumOfTrack?.date);

                return {
                    uri,
                    id,
                    name: t.name || "",
                    duration_ms: t.duration?.totalMilliseconds || 0,
                    popularity: parseInt(t.playcount || "0", 10),
                    track_number: t.trackNumber || 1,
                    album: t.albumOfTrack ? {
                        uri: t.albumOfTrack.uri,
                        id: t.albumOfTrack.uri.split(":").pop(),
                        release_date: releaseDate,
                    } : null,
                    artists: (t.firstArtist?.items || []).map(a => ({
                        name: a.profile?.name || "",
                        uri: a.uri || "",
                        id: (a.uri || "").split(":").pop(),
                    })),
                };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_track error:`, e);
                return { uri, id };
            }
        },

        async get_multiple_tracks({ track_ids }) {
            if (!track_ids?.length) return [];
            const results = await Promise.all(
                track_ids.map(id => handlers.get_track({ track_id: id }).catch(() => null))
            );

            const tracks = results.filter(Boolean);
            const albumIdsToFetch = new Set();
            for (const t of tracks) {
                if (t.album && !t.album.release_date && t.album.id) {
                    albumIdsToFetch.add(t.album.id);
                }
            }

            if (albumIdsToFetch.size > 0) {
                const albumPromises = Array.from(albumIdsToFetch).map(id =>
                    handlers.get_album({ album_id: id }).catch(() => null)
                );
                const albums = await Promise.all(albumPromises);

                const albumMap = {};
                for (const a of albums) {
                    if (a && a.id) albumMap[a.id] = a.release_date;
                }

                for (const t of tracks) {
                    if (t.album && !t.album.release_date && t.album.id && albumMap[t.album.id]) {
                        t.album.release_date = albumMap[t.album.id];
                    }
                }
            }

            return tracks;
        },

        // =====================================================================
        // PLAYLIST — read
        // =====================================================================
        async get_user_playlists({ limit = 50 }) {
            try {
                const rootlist = await Platform.RootlistAPI.getContents();
                if (!rootlist?.items) return [];

                const extractPlaylists = (items) => {
                    let result = [];
                    for (const item of items) {
                        if (item.type === "playlist") {
                            result.push(item);
                        } else if (item.type === "folder" && Array.isArray(item.items)) {
                            result.push(...extractPlaylists(item.items));
                        }
                    }
                    return result;
                };

                return extractPlaylists(rootlist.items)
                    .filter(p => p?.uri?.startsWith("spotify:playlist:") && p.isOwnedBySelf)
                    .slice(0, limit)
                    .map(p => ({
                        id: p.uri.split(":").pop(),
                        uri: p.uri,
                        name: p.name || "Playlist",
                        owner: { id: p.owner?.displayName || "" },
                        tracks: { total: p.totalLength || 0 },
                    }));
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_user_playlists error:`, e);
                return [];
            }
        },

        async get_playlist({ playlist_id }) {
            const uri = `spotify:playlist:${playlist_id}`;
            try {
                const meta = await Platform.PlaylistAPI.getMetadata(uri);
                return {
                    id: playlist_id,
                    uri,
                    name: meta?.name || "",
                    description: meta?.description || "",
                    owner: { id: meta?.owner?.displayName || "" },
                    tracks: { total: meta?.totalLength || 0 },
                    images: meta?.images || [],
                };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_playlist error:`, e);
                return null;
            }
        },

        async get_playlist_tracks({ playlist_id }) {
            const uri = `spotify:playlist:${playlist_id}`;
            try {
                const contents = await Platform.PlaylistAPI.getContents(uri);
                const items = contents?.items || [];

                return items.map(t => {
                    if (!t?.uri) return null;

                    const releaseDate = parseDateFields(t.album?.date);

                    return {
                        added_at: t.addedAt || null,
                        uid: t.uid || null,
                        track: {
                            uri: t.uri,
                            id: t.uri.split(":").pop(),
                            name: t.name || "",
                            artists: (t.artists || []).map(a => ({
                                name: a.name || "",
                                uri: a.uri || "",
                            })),
                            album: {
                                name: t.album?.name || "",
                                uri: t.album?.uri || "",
                                release_date: releaseDate,
                            },
                            duration_ms: t.duration?.milliseconds || 0,
                            popularity: 0,
                        },
                    };
                }).filter(Boolean);
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] get_playlist_tracks error:`, e);
                return [];
            }
        },

        // =====================================================================
        // PLAYLIST — write
        // =====================================================================
        async replace_playlist_tracks({ playlist_id, track_uris }) {
            const uri = `spotify:playlist:${playlist_id}`;
            try {
                const contents = await Platform.PlaylistAPI.getContents(uri);
                const items = contents?.items || [];
                const uidsToRemove = items
                    .map(t => ({ uid: t.uid, uri: t.uri }))
                    .filter(t => t.uid);

                for (let i = 0; i < uidsToRemove.length; i += 100) {
                    await Platform.PlaylistAPI.remove(uri, uidsToRemove.slice(i, i + 100));
                }

                if (track_uris && track_uris.length > 0) {
                    let addedCount = 0;
                    for (let i = 0; i < track_uris.length; i += 100) {
                        const batch = track_uris.slice(i, i + 100);
                        if (addedCount === 0) {
                            await Platform.PlaylistAPI.add(uri, batch, { before: "" });
                        } else {
                            const cur = await Platform.PlaylistAPI.getContents(uri);
                            const curItems = cur?.items || [];
                            const anchor = curItems[addedCount - 1];
                            const afterUid = anchor?.uid;
                            await Platform.PlaylistAPI.add(
                                uri, batch,
                                afterUid ? { after: afterUid } : { before: "" }
                            );
                        }
                        addedCount += batch.length;
                    }
                }
                return { success: true };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] replace_playlist_tracks error:`, e);
                return { success: false, error: String(e) };
            }
        },

        async create_playlist({ name, description = "", public: isPublic = false }) {
            try {
                const uri = await Platform.RootlistAPI.createPlaylist(name, { before: "" });
                if (description || isPublic != null) {
                    const attrs = {};
                    if (description) attrs.description = description;
                    if (isPublic != null) attrs.published = isPublic;
                    await Platform.PlaylistAPI.updateDetails(uri, attrs);
                }
                return { uri, id: uri.split(":").pop() };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] create_playlist error:`, e);
                return null;
            }
        },

        async add_tracks_to_playlist({ playlist_id, track_uris }) {
            if (!track_uris?.length) return { success: true };
            const uri = `spotify:playlist:${playlist_id}`;
            try {
                await Platform.PlaylistAPI.add(uri, track_uris, { before: "" });
                return { success: true };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] add_tracks_to_playlist error:`, e);
                return { success: false, error: String(e) };
            }
        },

        async remove_tracks_from_playlist({ playlist_id, track_uris }) {
            if (!track_uris?.length) return { success: true };
            const uri = `spotify:playlist:${playlist_id}`;

            try {
                const contents = await Platform.PlaylistAPI.getContents(uri);
                const items = contents?.items || [];
                const targets = new Set(track_uris);

                const removalObjects = items
                    .filter(t => targets.has(t.uri) && t.uid)
                    .map(t => ({ uid: t.uid, uri: t.uri }));

                if (removalObjects.length > 0) {
                    await Platform.PlaylistAPI.remove(uri, removalObjects);
                }
                return { success: true };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] remove_tracks_from_playlist error:`, e);
                return { success: false, error: String(e) };
            }
        },

        async update_playlist_details({ playlist_id, name, description, public: isPublic }) {
            const uri = `spotify:playlist:${playlist_id}`;
            const attrs = {};
            if (name != null) attrs.name = name;
            if (description != null) attrs.description = description;
            if (isPublic != null) attrs.published = isPublic;

            try {
                await Platform.PlaylistAPI.updateDetails(uri, attrs);
                return { success: true };
            } catch (e) {
                console.error(`[${EXTENSION_NAME}] update_playlist_details error:`, e);
                return { success: false, error: String(e) };
            }
        },
    };

    // =========================================================================
    // POLLING
    // =========================================================================
    async function poll() {
        if (!bridgeEnabled) return;

        try {
            const resp = await fetch(`${BASE_URL}/request`, {
                signal: AbortSignal.timeout(1000),
                cache: "no-store",
            }).catch(() => null);

            if (!resp) {
                if (bridgeConnected) {
                    bridgeConnected = false;
                    console.log(`[${EXTENSION_NAME}] Waiting for ANR server...`);
                }
                return;
            }

            if (resp.status === 204 || resp.status === 503) {
                if (!bridgeConnected) {
                    bridgeConnected = true;
                    if (!toastShown) {
                        Spicetify.showNotification("🎸 ANR Bridge active");
                        toastShown = true;
                    }
                    console.log(`[${EXTENSION_NAME}] Connected`);
                }
                return;
            }
            if (!resp.ok) return;

            const request = await resp.json();
            if (!request?.id || !request?.method) return;

            if (!bridgeConnected) {
                bridgeConnected = true;
                if (!toastShown) {
                    Spicetify.showNotification("🎸 ANR Bridge active");
                    toastShown = true;
                }
                console.log(`[${EXTENSION_NAME}] Connected`);
            }

            console.groupCollapsed(`[${EXTENSION_NAME}] ⚡ ${request.method}`);
            console.log("params:", request.params ?? {});

            let result = null;
            let error = null;
            try {
                const handler = handlers[request.method];
                if (!handler) throw new Error(`Unknown method: ${request.method}`);
                result = await handler(request.params ?? {});
            } catch (e) {
                error = e.message ?? String(e);
                console.error("error:", e);
            }

            await fetch(`${BASE_URL}/response`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: request.id, result, error }),
                signal: AbortSignal.timeout(5000),
            });

            console.log(error ? "❌ Failed" : "✅ Success", result);
            console.groupEnd();

        } catch {
            if (bridgeConnected) {
                bridgeConnected = false;
                console.warn(`[${EXTENSION_NAME}] Disconnected`);
            }
        }
    }

    // =========================================================================
    // MENU
    // =========================================================================
    function setupMenu() {
        if (!Spicetify?.Menu?.Item || !Spicetify?.React) {
            setTimeout(setupMenu, 300);
            return;
        }

        try {
            new Spicetify.Menu.Item(
                "ANR Bridge",
                bridgeEnabled,
                (self) => {
                    bridgeEnabled = !bridgeEnabled;
                    self.setState(bridgeEnabled);
                    Spicetify.LocalStorage.set(STORAGE_KEY, String(bridgeEnabled));

                    if (bridgeEnabled) {
                        Spicetify.showNotification("🎸 ANR Bridge Enabled");
                    } else {
                        Spicetify.showNotification("⏸️ ANR Bridge Disabled");
                        bridgeConnected = false;
                        toastShown = false;
                    }
                }
            ).register();
        } catch (e) {
            console.warn(`[${EXTENSION_NAME}] Menu not ready, retrying...`, e);
            setTimeout(setupMenu, 500);
        }
    }

    // =========================================================================
    // INIT
    // =========================================================================
    setupMenu();
    setInterval(poll, POLL_INTERVAL_MS);
    console.log(`[${EXTENSION_NAME}] Started — polling ${BASE_URL} every ${POLL_INTERVAL_MS}ms`);

})();