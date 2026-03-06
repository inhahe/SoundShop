def compile_for_server(project, *, sample_rate: int, block_size: int) -> ServerCommandPlan:
    # 1) compile tracks -> TrackIR with plugin keys and published endpoints
    track_ir = {tid: compile_track_to_ir(project.tracks[tid]) for tid in project.track_order}

    # 2) resolve project edges to channel edges
    channel_edges: list[AudioEdge] = []
    for c in project.connections.values():
        src_bundle = track_ir[c.src].published_out[c.src_port]  # list[Endpoint]
        dst_bundle = track_ir[c.dst].published_in[c.dst_port]   # list[Endpoint]
        channel_edges += zip_bundle(src_bundle, dst_bundle, gain_db=getattr(c, "gain_db", None))

    # 3) insert gain nodes where gain_db != None (stereo per bundle)
    plugins: dict[int, PluginSpec] = {}
    connections: list[tuple[int,int,int,int]] = []  # (srcKey,srcCh,dstKey,dstCh)
    gain_inits: list[tuple[int,int,float,int]] = []

    def add_plugin(spec: PluginSpec):
        plugins[spec.key] = spec

    # add all track plugins
    for ir in track_ir.values():
        for key, spec in ir.plugin_specs.items():
            add_plugin(spec)
        for e in ir.internal_audio_edges:
            connections.append((e.src.plugin_key, e.src.channel, e.dst.plugin_key, e.dst.channel))

    # group edges by (src_bundle_id, dst_bundle_id, gain_db) if you keep bundle ids;
    # simplest: detect stereo pairs by same srcKey/dstKey and channels 0/1
    # For now: assume every connect was stereo bundles and we got two edges with same gain_db.
    used = set()
    for i, e in enumerate(channel_edges):
        if i in used:
            continue
        if e.gain_db is None:
            connections.append((e.src.plugin_key, e.src.channel, e.dst.plugin_key, e.dst.channel))
            continue

        # try pair with same src/dst and channel+1 for stereo
        pair = None
        for j in range(i+1, len(channel_edges)):
            e2 = channel_edges[j]
            if e2.gain_db == e.gain_db and e2.src.plugin_key == e.src.plugin_key and e2.dst.plugin_key == e.dst.plugin_key:
                if {e.src.channel, e2.src.channel} == {0,1} and {e.dst.channel, e2.dst.channel} == {0,1}:
                    pair = (j, e2)
                    break

        gain_key = allocate_unique_key()  # you do this in your scheduler
        add_plugin(PluginSpec(uid=UID_GAIN, key=gain_key, kind="utility_gain"))

        # connect channels through gain
        # channel 0
        connections.append((e.src.plugin_key, 0, gain_key, 0))
        connections.append((gain_key, 0, e.dst.plugin_key, 0))
        # channel 1 (if paired)
        if pair is not None:
            j, e2 = pair
            used.add(j)
            connections.append((e.src.plugin_key, 1, gain_key, 1))
            connections.append((gain_key, 1, e.dst.plugin_key, 1))

        used.add(i)

        # schedule initial gain param at block 0
        linear = 10 ** (e.gain_db / 20.0)
        gain_inits.append((gain_key, 0, linear, 0))
# 4) schedule events (notes/cc/params) to plugin keys (then later map to pluginIds)
    notes, ccs, params = schedule_events(project, track_ir, sample_rate, block_size)

    return ServerCommandPlan(
        plugins_in_load_order=[plugins[k] for k in sorted(plugins.keys())],  # choose stable order
        audio_connections=connections,
        gain_inits=gain_inits,
        midi_notes=notes,
        midi_ccs=ccs,
        param_changes=params,
    )