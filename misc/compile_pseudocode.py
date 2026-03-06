def compile_to_juce_plan(project) -> RenderPlan:
    # 1) validate ports/cycles in authoring space if you want
    project.compile(strict_ports=True, detect_cycles=True, freeze_ports=True)

    # 2) compile tracks to TrackIR (plugins + published ports)
    track_ir: dict[int, TrackIR] = {}
    for tid in project.track_order:
        track_ir[tid] = compile_track_to_ir(project.tracks[tid])

    # 3) collect all plugin specs (node_uid -> plugin)
    all_plugins: dict[int, PluginSpec] = {}
    for ir in track_ir.values():
        all_plugins.update(ir.plugin_specs)

    # 4) resolve project connections into concrete channel edges
    resolved_edges: list[ResolvedEdge] = []
    for conn in project.connections.values():
        src_eps = track_ir[conn.src].published_out[conn.src_port]   # list[Endpoint]
        dst_eps = track_ir[conn.dst].published_in[conn.dst_port]    # list[Endpoint]
        resolved_edges += expand_bundle_edges(src_eps, dst_eps, gain_db=conn.gain_db)

    # 5) insert explicit mixers for fan-in (optional but recommended)
    resolved_edges, mixer_nodes = insert_mixers_for_fanin(resolved_edges)

    # 6) insert gain nodes where requested
    resolved_edges, gain_nodes = insert_gain_nodes(resolved_edges)

    # 7) final node set = plugins + mixers + gains
    all_nodes = {**all_plugins, **mixer_nodes, **gain_nodes}

    # 8) build JUCE graph nodes and keep mapping node_uid->juceNodeId
    juce_node_id = {}
    for node_uid, spec in all_nodes.items():
        juce_node_id[node_uid] = renderer_create_node(spec)  # your renderer-side call

    # 9) emit JUCE connections
    juce_edges = []
    for e in resolved_edges:
        if e.kind == "midi":
            juce_edges.append(make_midi_connection(juce_node_id[e.src_uid], juce_node_id[e.dst_uid]))
        else:
            juce_edges.append(make_audio_connection(juce_node_id[e.src_uid], e.src_ch,
                                                   juce_node_id[e.dst_uid], e.dst_ch))

    # 10) schedule events to node_uid targets (your existing scheduling)
    notes, ccs, params = schedule_all_events(project, track_ir)

    return RenderPlan(nodes=all_nodes, edges=juce_edges,
                      node_id_map=juce_node_id,
                      notes=notes, ccs=ccs, params=params)

"""
What insert_gain_nodes() does
  For each audio edge with gain_db is not None:
  create a GainSpec(node_uid=...) utility node
  replace the edge with two edges via gain node
  store the gain value as initial parameter state of that gain node (or as “parameter event at time 0”)
What insert_mixers_for_fanin() does
  Group edges by destination endpoint (dst_uid, dst_ch)
  If more than 1 incoming:
    create a mixer node per destination bundle (often stereo)
    redirect all incoming into mixer, then mixer into original destination
"""
