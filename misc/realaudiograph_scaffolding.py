class RealAudioGraph(PortGraph):
    def input_ports(self) -> Set[str]:
        return set(self.input_port_nodes.keys())

    def output_ports(self) -> Set[str]:
        return set(self.output_port_nodes.keys())

    def ensure_input_port(self, name: str) -> None:
        if name not in self.input_port_nodes:
            self.input_port_nodes[name] = InputPort(name)

    def ensure_output_port(self, name: str) -> None:
        if name not in self.output_port_nodes:
            # could create an OutputPort with a placeholder source
            self.output_port_nodes[name] = OutputPort(name, src=None)