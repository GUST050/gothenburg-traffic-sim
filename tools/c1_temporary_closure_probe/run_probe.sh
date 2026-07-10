#!/bin/zsh
# Isolated C1 SUMO probe.  Nothing here is read by the Gothenburg pipeline.
set -euo pipefail

ROOT=${0:A:h}
SUMO_HOME="$(python3 -c 'import sumo, pathlib; print(pathlib.Path(sumo.__file__).resolve().parent)')"
SUMO="$SUMO_HOME/bin/sumo"
NETCONVERT="$SUMO_HOME/bin/netconvert"
export SUMO_HOME

rm -rf "$ROOT/out"
mkdir -p "$ROOT/out"

cat > "$ROOT/net.nod.xml" <<'EOF'
<nodes>
  <node id="a" x="0" y="0" type="priority"/><node id="b" x="50" y="0" type="priority"/>
  <node id="c" x="100" y="0" type="priority"/><node id="d" x="150" y="0" type="priority"/>
  <node id="e" x="75" y="500" type="priority"/>
  <node id="w" x="0" y="-100" type="priority"/><node id="x" x="50" y="-100" type="priority"/>
  <node id="y" x="100" y="-100" type="priority"/><node id="z" x="150" y="-100" type="priority"/>
</nodes>
EOF
cat > "$ROOT/net.edg.xml" <<'EOF'
<edges>
  <edge id="direct_in" from="a" to="b" numLanes="1" speed="10"/>
  <edge id="closed" from="b" to="c" numLanes="1" speed="10"/>
  <edge id="direct_out" from="c" to="d" numLanes="1" speed="10"/>
  <edge id="detour_1" from="b" to="e" numLanes="1" speed="10"/>
  <edge id="detour_2" from="e" to="c" numLanes="1" speed="10"/>
  <edge id="no_in" from="w" to="x" numLanes="1" speed="10"/>
  <edge id="no_closed" from="x" to="y" numLanes="1" speed="10"/>
  <edge id="no_out" from="y" to="z" numLanes="1" speed="10"/>
</edges>
EOF
"$NETCONVERT" -n "$ROOT/net.nod.xml" -e "$ROOT/net.edg.xml" -o "$ROOT/net.net.xml" --no-turnarounds true > "$ROOT/out/netconvert.log" 2>&1

cat > "$ROOT/short.rou.xml" <<'EOF'
<routes>
  <vType id="car" accel="2.6" decel="4.5" length="5" maxSpeed="10"/>
  <!-- crosses before closure; enters during closure and is rerouted; enters after reopening -->
  <vehicle id="direct_pre" type="car" depart="0"><route edges="direct_in closed direct_out"/></vehicle>
  <vehicle id="rerouted" type="car" depart="20"><route edges="direct_in closed direct_out"/></vehicle>
  <!-- no_in is the rerouter edge; no alternative exists around no_closed -->
  <vehicle id="stranded_short" type="car" depart="20"><route edges="no_in no_closed no_out"/></vehicle>
  <vehicle id="reopened_direct" type="car" depart="110"><route edges="direct_in closed direct_out"/></vehicle>
</routes>
EOF
cat > "$ROOT/long.rou.xml" <<'EOF'
<routes>
  <vType id="car" accel="2.6" decel="4.5" length="5" maxSpeed="10"/>
  <vehicle id="stranded_long" type="car" depart="20"><route edges="no_in no_closed no_out"/></vehicle>
</routes>
EOF
cat > "$ROOT/short.add.xml" <<'EOF'
<additional>
  <rerouter id="closure" edges="direct_in no_in">
    <interval begin="10" end="100"><closingReroute id="closed" disallow="all"/><closingReroute id="no_closed" disallow="all"/></interval>
  </rerouter>
</additional>
EOF
cat > "$ROOT/long.add.xml" <<'EOF'
<additional>
  <rerouter id="closure" edges="no_in">
    <interval begin="10" end="500"><closingReroute id="no_closed" disallow="all"/></interval>
  </rerouter>
</additional>
EOF

run() {
  local name=$1 route=$2 add=$3; shift 3
  "$SUMO" -n "$ROOT/net.net.xml" -r "$ROOT/$route" -a "$ROOT/$add" \
    --tripinfo-output "$ROOT/out/$name.tripinfo.xml" \
    --vehroute-output "$ROOT/out/$name.vehroute.xml" --vehroute-output.exit-times true \
    --end 700 --no-step-log true "$@" > "$ROOT/out/$name.log" 2>&1
}

# Current production flag: it merely suppresses static route-connectivity validation.
run short_ignore short.rou.xml short.add.xml --ignore-route-errors true
# SUMO 1.27.1 --help: mode 8 is set on *rerouting devices*, so give every
# vehicle one and reroute each second.  No --ignore-route-errors here.
run short_mode8 short.rou.xml short.add.xml --device.rerouting.probability 1 --device.rerouting.mode 8 --device.rerouting.period 1
# Default --time-to-teleport is intentionally not overridden.
run long_ignore long.rou.xml long.add.xml --ignore-route-errors true

echo "Outputs written to $ROOT/out"
