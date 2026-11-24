# Run automatically by Unreal's Python plugin for every init_unreal.py on its sys.path.
# This folder gets there via UE_PYTHONPATH: set per launch by `ue.py editor start`, or for every
# editor of every project by `ue.py bridge install`. Each project gets its own Saved/AgentBridge.
import agent_bridge

agent_bridge.start()
