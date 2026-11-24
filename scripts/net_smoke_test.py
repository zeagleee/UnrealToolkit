"""Example `ue.py net run` script: every process agrees on who is connected, and nobody logged a net error.

    ue.py net start --clients 2 && ue.py net run <toolkit folder>/scripts/net_smoke_test.py

`net` is a uetk.net.NetSession: net.server / net.clients / net["client1"], each with .py(code), .exec(cmd),
.info(), .wait(expr); net.wait_players(n); net.log(grep=..., errors=True). Raise (or assert) to fail.
"""
expected = len(net.clients) + (0 if net.session["dedicated"] else 1)
players = net.wait_players(expected, timeout=60)
print(f"server sees {len(players)} players")

for instance in [net.server] + net.clients:
    info = instance.info()
    assert len(info["players"]) == expected, f"{instance.name} sees {info['players']}"
    print(f"{instance.name:8} {info['role']:15} sees {len(info['players'])} players, t={info['time']}s")

joins = net.log(grep="Join succeeded", tail=0)
assert len(joins) == len(net.clients), "\n".join(joins)
problems = [line for line in net.log(errors=True, tail=0) if "LogNet" in line and "Error" in line]
assert not problems, "net errors:\n" + "\n".join(problems[-10:])
print("ok: everyone connected, no LogNet errors")
