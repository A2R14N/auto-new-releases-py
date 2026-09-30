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
    let sessionToken = null;
    let polling = false;
    let disposed = false;
    let menuItem = null;
    let menuRetry = null;
    globalThis.__anrBridge?.dispose();

    // Read every row, including unavailable/local entries. Never write after
    // an incomplete read: a missing page must not silently lose songs.
    async function playlistRows(uri) {
        const rows = [];
        let total = null;
        do {
            const page = await Platform.PlaylistAPI.getContents(uri, { offset: rows.length, limit: 500 });
            if (!Array.isArray(page?.items) || !Number.isInteger(page.totalLength)) {
                throw new Error("Could not read complete playlist contents");
            }
            if (total !== null && total !== page.totalLength) throw new Error("Playlist changed while reading");
            total = page.totalLength;
            if (!page.items.length && rows.length < total) throw new Error("Incomplete playlist page");
            rows.push(...page.items);
        } while (rows.length < total);
        if (rows.length !== total || rows.some(row => !row.uid || !row.uri)) {
            throw new Error("Playlist contains unreadable rows");
        }
        return rows;
    }

    async function verifyOrder(uri, uris, uids = null) {
        if (typeof Platform.PlaylistAPI.resync === "function") await Platform.PlaylistAPI.resync(uri);
        const rows = await playlistRows(uri);
        if (rows.length !== uris.length || rows.some((row, i) => row.uri !== uris[i] || (uids && row.uid !== uids[i]))) {
            throw new Error("Spotify did not save the requested playlist order");
        }
    }

    async function rewritePlaylist(uri, rows, uris) {
        for (let i = 0; i < rows.length; i += 100) {
            await Platform.PlaylistAPI.remove(uri, rows.slice(i, i + 100).map(row => ({ uid: row.uid })));
        }
        // Explicit end sentinel; UID anchors must be objects in this client.
        for (let i = 0; i < uris.length; i += 100) {
            await Platform.PlaylistAPI.add(uri, uris.slice(i, i + 100), { after: "end" });
        }
        await verifyOrder(uri, uris);
    }

    function planMoves(rows, wanted) {
        const current = rows.map(row => row.uid);
        const moves = [];
        for (let i = 0; i < wanted.length;) {
            if (current[i] === wanted[i]) { i++; continue; }
            const positions = new Map(current.map((uid, index) => [uid, index]));
            let end = i + 1;
            // Spotify preserves source order within a move. Only group rows
            // whose relative order already matches the requested order.
            while (end < wanted.length && end - i < 100 && positions.get(wanted[end]) > positions.get(wanted[end - 1])) end++;
            const group = wanted.slice(i, end);
            moves.push({ uids: group, location: i ? { after: { uid: wanted[i - 1] } } : { before: "start" } });
            const selected = new Set(group);
            const remaining = current.filter(uid => !selected.has(uid));
            current.splice(0, current.length, ...remaining.slice(0, i), ...group, ...remaining.slice(i));
            i = end;
        }
        return moves;
    }

    // NORMALIZERS
    function normalizeFollowerCount(value) {
        if (typeof value === "number" && Number.isFinite(value)) return value;
        if (!value || typeof value !== "object") return null;
        for (const candidate of [value.total, value.count, value.value]) {
            if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
        }
        return null;
    }

    function normalizeSearchArtist(hit) {
        if (!hit) return null;
        const d = hit.data ?? hit;
        if (!d?.uri) return null;
        return {
            uri: d.uri,
            id: d.uri.split(":").pop(),
            name: d.profile?.name ?? d.name ?? "",
            followers: {
                total: normalizeFollowerCount(
                    d.stats?.followers ?? d.stats?.followersCount ?? d.followers
                ),
            },
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
                    followers: {
                        total: normalizeFollowerCount(
                            a.stats?.followers ?? a.stats?.followersCount ?? a.followers
                        ),
                    },
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
                const items = await playlistRows(uri);

                return items.map(t => {
                    if (!t?.uri) return null;

                    const releaseDate = parseDateFields(t.release || t.album?.date);

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
                if (!Array.isArray(track_uris)) throw new Error("Missing track list");
                await rewritePlaylist(uri, await playlistRows(uri), track_uris);
                return { success: true };
            } catch (e) {
                return { success: false, error: String(e) };
            }
        },

        async reorder_playlist_tracks({ playlist_id, track_uris, track_uids, expected_uris, expected_uids }) {
            const uri = `spotify:playlist:${playlist_id}`;
            try {
                const rows = await playlistRows(uri);
                if (!Array.isArray(track_uris) || !Array.isArray(expected_uris) ||
                    rows.length !== expected_uris.length || rows.some((row, i) =>
                        row.uri !== expected_uris[i] || (expected_uids?.length && row.uid !== expected_uids[i]))) {
                    throw new Error("Playlist changed since sorting began; retry the sort");
                }
                const byUri = new Map();
                rows.forEach(row => {
                    if (!byUri.has(row.uri)) byUri.set(row.uri, []);
                    byUri.get(row.uri).push(row.uid);
                });
                const wanted = track_uids?.length ? track_uids : track_uris.map(uri => byUri.get(uri)?.shift());
                const rowMap = new Map(rows.map(row => [row.uid, row]));
                if (wanted.length !== rows.length || new Set(wanted).size !== rows.length ||
                    wanted.some((uid, i) => !uid || rowMap.get(uid)?.uri !== track_uris[i])) {
                    throw new Error("Sorted rows do not match the playlist");
                }
                const moves = planMoves(rows, wanted);
                const rewriteWrites = 2 * Math.ceil(rows.length / 100);
                let strategy = "move";
                if (!moves.length) strategy = "unchanged";
                else if (typeof Platform.PlaylistAPI.move === "function" && moves.length <= rewriteWrites) {
                    for (const move of moves) {
                        await Platform.PlaylistAPI.move(uri, move.uids.map(uid => ({ uid })), move.location);
                    }
                    await verifyOrder(uri, track_uris, wanted);
                } else {
                    strategy = "rewrite";
                    await rewritePlaylist(uri, rows, track_uris);
                }
                return { success: true, strategy, writes: strategy === "move" ? moves.length : strategy === "rewrite" ? rewriteWrites : 0 };
            } catch (e) {
                return { success: false, error: String(e) };
            }
        },

        async show_notification({ message, is_error = false, duration_ms = 6000 }) {
            if (typeof message !== "string" || !message.trim()) return { success: false };
            // Templates and profile names are plain text, not toast HTML.
            const safeText = message.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
            Spicetify.showNotification(safeText, Boolean(is_error), Math.max(1000, Math.min(30000, Number(duration_ms) || 6000)));
            return { success: true };
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
                await Platform.PlaylistAPI.add(uri, track_uris, { before: "start" });
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
        if (!bridgeEnabled || polling || disposed) return;
        polling = true;

        try {
            if (!sessionToken) {
                const status = await fetch(`${BASE_URL}/status`, {
                    signal: AbortSignal.timeout(1000),
                    cache: "no-store",
                }).catch(() => null);
                if (!status?.ok) return;
                const session = await status.json();
                sessionToken = session?.session_token ?? null;
                if (!sessionToken) return;
            }

            const resp = await fetch(`${BASE_URL}/request`, {
                headers: { "X-ANR-Token": sessionToken },
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

            if (resp.status === 401) {
                sessionToken = null;
                bridgeConnected = false;
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
                headers: {
                    "Content-Type": "application/json",
                    "X-ANR-Token": sessionToken,
                },
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
        } finally {
            polling = false;
        }
    }

    // =========================================================================
    // MENU
    // =========================================================================
    function setupMenu() {
        if (disposed) return;
        if (!Spicetify?.Menu?.Item || !Spicetify?.React) {
            menuRetry = setTimeout(setupMenu, 300);
            return;
        }

        try {
            menuItem = new Spicetify.Menu.Item(
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
                        sessionToken = null;
                    }
                }
            );
            menuItem.register();
        } catch (e) {
            console.warn(`[${EXTENSION_NAME}] Menu not ready, retrying...`, e);
            menuRetry = setTimeout(setupMenu, 500);
        }
    }

    // =========================================================================
    // INIT
    // =========================================================================
    setupMenu();
    const pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    globalThis.__anrBridge = {
        handlers,
        dispose() {
            disposed = true;
            clearInterval(pollTimer);
            clearTimeout(menuRetry);
            menuItem?.deregister();
        },
    };
    console.log(`[${EXTENSION_NAME}] Started — polling ${BASE_URL} every ${POLL_INTERVAL_MS}ms`);

})();
