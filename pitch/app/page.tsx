'use client';

// Inline SVG diagrams need role=img for an accessible name; an img element cannot contain SVG geometry.
/* oxlint-disable jsx-a11y/prefer-tag-over-role */

import { useEffect, useState } from 'react';
import {
  ArrowDown,
  ArrowUpRight,
  Radio,
  Crosshair,
  EyeOff,
  Network,
  Pause,
  Play,
  ChevronRight,
  Check,
  GitBranch,
  MoveUpRight,
} from 'lucide-react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

const REPO = 'https://github.com/yav-fi/tactlink/blob/main/';
const nodes = [
  [340, 95],
  [515, 215],
  [445, 410],
  [220, 400],
  [145, 205],
];
const colors = ['#b4ef74', '#7dd3c5', '#c6d9f1', '#c3b5f4', '#efc783'];
// Circle-method schedule used by RoundRobin.rounds in RoomTypes.swift.
const rounds = [
  [
    [1, 4],
    [2, 3],
  ],
  [
    [0, 4],
    [1, 2],
  ],
  [
    [0, 3],
    [4, 2],
  ],
  [
    [0, 2],
    [3, 1],
  ],
  [
    [0, 1],
    [3, 4],
  ],
];
const letters = 'ABCDE';

function Mark() {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 2 29 9.5v13L16 30 3 22.5v-13Z" stroke="currentColor" />
      <path
        d="m3 9.5 13 7 13-7M16 16.5V30M9.5 6l13 7.5v12"
        stroke="currentColor"
      />
      <circle cx="16" cy="16" r="3" fill="currentColor" />
    </svg>
  );
}
function Tag({ children }: { children: React.ReactNode }) {
  return <span className="eyebrow">{children}</span>;
}
function Source({
  path,
  children,
}: {
  path: string;
  children: React.ReactNode;
}) {
  return (
    <a className="source" href={REPO + path} target="_blank" rel="noreferrer">
      {children} <ArrowUpRight size={13} />
    </a>
  );
}

function Geometry({ compact = false }: { compact?: boolean }) {
  const [selected, setSelected] = useState(4);
  // Five distance constraints share a common point. Wireframe projections are explanatory, not live telemetry.
  const centers = [
    [220, 170],
    [415, 150],
    [452, 320],
    [253, 355],
    [193, 286],
  ].map(([x, y]) => [x, y, Math.hypot(333 - x, 255 - y)]);
  return (
    <div className={`geometry ${compact ? 'compact' : ''}`}>
      <div className="figure-top">
        <span>
          <i className="status-dot" /> RELATIVE REFERENCE FRAME
        </span>
        <span>FIG. 01 / GEOMETRY</span>
      </div>
      <svg
        viewBox="0 0 650 490"
        role="img"
        aria-label="Five schematic range spheres intersect at a single relative position; colored shells represent independent distance constraints."
      >
        <defs>
          <radialGradient id={compact ? 'glow-c' : 'glow'}>
            <stop stopColor="#b4ef74" stopOpacity=".18" />
            <stop offset="1" stopColor="#b4ef74" stopOpacity="0" />
          </radialGradient>
        </defs>
        <g stroke="#647161" strokeWidth=".6" opacity=".25">
          {Array.from({ length: 13 }, (_, i) => (
            <path
              key={i}
              d={`M${30 + i * 44} 330 l180 105 M${110 + i * 36} 270 l-150 165`}
            />
          ))}
        </g>
        <ellipse
          cx="333"
          cy="350"
          rx="275"
          ry="95"
          fill={`url(#${compact ? 'glow-c' : 'glow'})`}
        />
        <g stroke="#82917a" strokeDasharray="3 6" opacity=".5">
          <path d="M333 255v183m0-183L80 380m253-125 250 100" />
        </g>
        <g fill="#7b8875" fontSize="12" fontFamily="monospace">
          <text x="335" y="451">
            z
          </text>
          <text x="63" y="389">
            x
          </text>
          <text x="588" y="361">
            y
          </text>
        </g>
        {centers.map(([x, y, r], i) => (
          <g
            key={i}
            stroke={colors[i]}
            fill="none"
            opacity={i <= selected ? (i === selected ? 0.8 : 0.34) : 0.06}
            className="shell"
          >
            <circle
              cx={x}
              cy={y}
              r={r}
              fill={colors[i]}
              fillOpacity=".022"
              strokeWidth=".8"
            />
            <ellipse cx={x} cy={y} rx={r} ry={r * 0.34} strokeWidth=".7" />
            <ellipse
              cx={x}
              cy={y}
              rx={r * 0.35}
              ry={r}
              transform={`rotate(${i * 30 - 35} ${x} ${y})`}
              strokeWidth=".7"
            />
            <path
              d={`M${x} ${y}L333 255`}
              strokeDasharray="3 4"
              strokeWidth=".8"
            />
            <circle cx={x} cy={y} r="4" fill={colors[i]} />
            <text
              x={x - 13}
              y={y - 13}
              fill={colors[i]}
              stroke="none"
              fontSize="12"
              fontFamily="monospace"
            >
              r{i + 1}
            </text>
          </g>
        ))}
        <circle cx="333" cy="255" r="19" fill="#b4ef74" fillOpacity=".1" />
        <circle cx="333" cy="255" r="7" fill="#b4ef74" />
        <path d="M333 237v-10m0 46v10m-18-28h-10m46 0h10" stroke="#d4f5b3" />
        <path
          d="M349 253h87l30-32h119"
          stroke="#b4ef74"
          strokeWidth=".7"
          fill="none"
        />
        <text
          x="474"
          y="208"
          fill="#dcf5c8"
          fontSize="12"
          fontFamily="monospace"
        >
          POSITION ESTIMATE
        </text>
        <text
          x="474"
          y="238"
          fill="#82917a"
          fontSize="12"
          fontFamily="monospace"
        >
          p = (x, y, z)
        </text>
      </svg>
      <div className="figure-bottom">
        <span>‖p − pᵢ‖ = rᵢ</span>
        <span>RANGE CONSTRAINTS / SCHEMATIC</span>
      </div>
      {!compact && (
        <div className="constraint-controls">
          <span>Add a range</span>
          <div>
            {centers.map((_, i) => (
              <button
                key={i}
                onClick={() => setSelected(i)}
                aria-pressed={selected === i}
                aria-label={`Show ${i + 1} range constraints`}
              >
                {String(i + 1).padStart(2, '0')}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RoundRobin() {
  const [round, setRound] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setRound((r) => (r + 1) % 5), 1400);
    return () => clearInterval(timer);
  }, [playing]);
  const active = rounds[round];
  const busy = active.flat();
  return (
    <div className="scheduler">
      <div className="schedule-map">
        <div className="figure-top">
          <span>ONE RADIO · ONE PARTNER</span>
          <span>FIG. 03</span>
        </div>
        <svg
          viewBox="0 0 650 490"
          role="img"
          aria-label={`Round ${round + 1}: ${active.map(([a, b]) => `${letters[a]} ranges with ${letters[b]}`).join(', ')}. ${letters[[0, 1, 2, 3, 4].find((i) => !busy.includes(i))!]} waits.`}
        >
          {nodes.flatMap(([x, y], i) =>
            nodes
              .slice(i + 1)
              .map(([x2, y2], j) => (
                <line
                  key={`${i}-${j}`}
                  x1={x}
                  y1={y}
                  x2={x2}
                  y2={y2}
                  stroke="#344032"
                  strokeDasharray="3 7"
                />
              )),
          )}
          {active.map(([a, b], i) => (
            <g key={`${round}-${i}`}>
              <line
                x1={nodes[a][0]}
                y1={nodes[a][1]}
                x2={nodes[b][0]}
                y2={nodes[b][1]}
                stroke={i === 0 ? '#b4ef74' : '#7dd3c5'}
                strokeWidth="2"
              />
              <circle
                cx={(nodes[a][0] + nodes[b][0]) / 2}
                cy={(nodes[a][1] + nodes[b][1]) / 2}
                r="4"
                fill={i === 0 ? '#b4ef74' : '#7dd3c5'}
              />
            </g>
          ))}
          {nodes.map(([x, y], i) => (
            <g key={i}>
              <circle
                cx={x}
                cy={y}
                r="31"
                fill="#121b12"
                stroke={busy.includes(i) ? '#b4ef74' : '#46513e'}
              />
              <text
                x={x}
                y={y + 6}
                fill={busy.includes(i) ? '#e1f1d5' : '#879280'}
                fontSize="19"
                textAnchor="middle"
                fontFamily="monospace"
              >
                {letters[i]}
              </text>
              <text
                x={x}
                y={y + 54}
                fill="#91a086"
                textAnchor="middle"
                fontSize="12"
                fontFamily="monospace"
              >
                {busy.includes(i) ? 'RANGING' : 'WAITING'}
              </text>
            </g>
          ))}
        </svg>
        <div className="figure-bottom">
          <span>{String(round + 1).padStart(2, '0')} / 05 ROUNDS</span>
          <button
            onClick={() => setPlaying(!playing)}
            aria-label={
              playing ? 'Pause round animation' : 'Play round animation'
            }
          >
            {playing ? <Pause size={13} /> : <Play size={13} />}{' '}
            {playing ? 'Pause' : 'Play schedule'}
          </button>
        </div>
      </div>
      <div className="schedule-details">
        <Tag>TIME-DIVISION MULTIPLEXING</Tag>
        <h3>
          One connection.
          <br />
          Every peer.
        </h3>
        <p>
          A phone ranges with one partner at a time. Rotate the pairings; two
          independent pairs can work in parallel while the fifth phone waits.
        </p>
        <div className="round-buttons" aria-label="Select a ranging round">
          {rounds.map((pairs, i) => (
            <button
              key={i}
              onClick={() => {
                setRound(i);
                setPlaying(false);
              }}
              aria-pressed={round === i}
            >
              <span>0{i + 1}</span>
              <span>
                {pairs
                  .map(([a, b]) => `${letters[a]} ↔ ${letters[b]}`)
                  .join('   /   ')}
              </span>
              {round === i ? (
                <ChevronRight size={16} />
              ) : (
                <span className="tiny-dot" />
              )}
            </button>
          ))}
        </div>
        <div className="schedule-summary">
          <span>
            <b>5</b> peers
          </span>
          <span>
            <b>10</b> unique pairs
          </span>
          <span>
            <b>5</b> nominal rounds
          </span>
        </div>
        <p className="fine">
          Actual dispatch is availability-based: a free pair may start without
          waiting for an entire round. Each attempt exchanges fresh Nearby
          Interaction tokens.
        </p>
      </div>
    </div>
  );
}

const trainSteps = [
  ['01', 'Labeled poses', 'Record examples of each gesture from a webcam.'],
  [
    '02',
    'MediaPipe landmarks',
    'Detect the palm → crop the hand → extract 21 × XYZ landmarks.',
  ],
  [
    '03',
    '63-D features',
    'Mirror left hands, center the wrist, normalize hand scale.',
  ],
  [
    '04',
    'Fit custom k-NN',
    'Standardize features; store labeled examples, k = 5, and rejection radius.',
  ],
  [
    '05',
    'Model artifact',
    '.npz: examples + labels + normalization + feature version.',
  ],
];
const inferSteps = [
  [
    '01',
    'Local camera',
    'Frames stay on the device; no image stream to the swarm.',
  ],
  [
    '02',
    'MediaPipe recognizer',
    'Palm detection → 21 landmarks → canned gesture scores.',
  ],
  [
    '03',
    'Gesture logic',
    'Phone demo: three landmark-based poses. Desktop: optional custom k-NN.',
  ],
  [
    '04',
    'Stabilize & gate',
    'Confidence rejection, recent-frame voting, and command hold.',
  ],
  [
    '05',
    'Command output',
    'Gesture + confidence + heading joins the UWB position stream.',
  ],
];
function Pipeline({ training }: { training: boolean }) {
  return (
    <>
      <div className="pipeline">
        {(training ? trainSteps : inferSteps).map(([n, title, desc]) => (
          <div className="pipeline-step" key={n}>
            <span className="pipeline-number">{n}</span>
            <h4>{title}</h4>
            <p>{desc}</p>
            <ChevronRight className="pipeline-arrow" size={17} />
          </div>
        ))}
      </div>
      <div className="pipeline-note">
        <span className="mono">
          {training
            ? 'TRAIN ONCE · REUSE FEATURES'
            : 'RUN LOCALLY · TRANSMIT INTENT'}
        </span>
        <p>
          {training
            ? 'MediaPipe’s pretrained landmark model stays fixed. The repository’s optional desktop training script fits a small gesture classifier on top; it does not retrain the vision backbone. No custom trained artifact is included.'
            : 'The phone prototype runs MediaPipe locally, interprets finger landmarks, and stabilizes the resulting command. It stands in for a future sensing platform; recognition from a camera onboard a flying drone has not been demonstrated. Gesture visibility does not determine UWB position.'}
        </p>
      </div>
    </>
  );
}

export default function Home() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <a className="brand" href="#main">
          <Mark />
          TactLink
        </a>
        <nav aria-label="Main navigation">
          <a href="#squadmate">The idea</a>
          <a href="#prototype">The prototype</a>
          <a href="#positioning">The technology</a>
        </nav>
        <a className="header-link" href="#performance">
          What comes next <ArrowUpRight size={15} />
        </a>
      </header>
      <main id="main">
        <section className="hero wrap">
          <div className="hero-copy">
            <Tag>
              <i className="status-dot" /> SQUAD–DRONE INTERACTION
            </Tag>
            <h1>
              Add the drone
              <br />
              to the <span>squad.</span>
            </h1>
            <p className="hero-description">
              Plan the mission. Signal changes as the squad moves.
              <br />
              TactLink brings shared spatial awareness and human intent
              together, with a vision of a drone that works alongside the team
              as another squadmate.
            </p>
            <div className="hero-actions">
              <a className="primary-link" href="#squadmate">
                Meet the idea <ArrowDown size={17} />
              </a>
              <a className="text-link" href="#prototype">
                See what works today <ArrowUpRight size={16} />
              </a>
            </div>
            <div className="hero-tags">
              <span>
                <EyeOff size={14} /> Shared spatial awareness
              </span>
              <span>
                <Network size={14} /> Human-directed support
              </span>
            </div>
          </div>
          <Geometry compact />
        </section>
        <div className="spec-strip wrap">
          <div>
            <strong>Plan</strong>
            <span>Give the mission a starting point</span>
          </div>
          <div>
            <strong>Signal</strong>
            <span>Express a change through a gesture</span>
          </div>
          <div>
            <strong>Share</strong>
            <span>Pass control between teammates</span>
          </div>
          <div>
            <strong>Adapt</strong>
            <span>Keep the human in the loop</span>
          </div>
        </div>
        <section className="context wrap" id="squadmate">
          <Tag>WHY IT MATTERS</Tag>
          <div>
            <h2>
              The squad needs to adapt.
              <br />
              Eyes stay up.
            </h2>
            <p>
              A drone can add another perspective. Directing it also asks for
              attention: a screen to check, an interface to operate, a change to
              communicate. TactLink explores a more natural relationship: a
              shared drone that responds to the squad’s intent while people stay
              engaged with one another and their surroundings.
            </p>
            <p className="fine">
              The goal is to make drone support feel like teamwork. Our
              connected simulation tests that interaction today; physical drone
              integration is the next step.
            </p>
          </div>
          <div className="context-list">
            <span>
              <Check size={17} /> A shared mission
            </span>
            <span>
              <Check size={17} /> Clear human intent
            </span>
            <span>
              <Check size={17} /> Control that can pass
            </span>
          </div>
        </section>
        <section className="section wrap" id="prototype">
          <div className="section-heading">
            <div>
              <Tag>01 / THE WORKING PROTOTYPE</Tag>
              <h2>
                Real people.
                <br />
                A shared virtual drone.
              </h2>
            </div>
            <p>
              The phones stand in for the squad’s sensors.
              <br />
              The simulator stands in for the aircraft.
            </p>
          </div>
          <p>
            We do not have drone hardware yet. Connected iPhones let us
            represent people moving through the world, capture their gestures,
            and send their state to a shared 3D simulation on a computer. This
            is a way to test the squadmate interaction before integrating a
            physical aircraft.
          </p>
          <div className="metric-cards">
            <article>
              <Tag>01 / PLAN</Tag>
              <h3>Give the mission a starting point.</h3>
              <p>
                Build a route in the 2D flight planner and explore it in the 3D
                simulator. The project brings planned missions and live operator
                input into the same virtual world.
              </p>
              <span className="metric-status">PLANNER + SIMULATION</span>
            </article>
            <article>
              <Tag>02 / SIGNAL</Tag>
              <h3>Make a change with a gesture.</h3>
              <p>
                In the current phone demo, a fist calls the simulated drone to
                follow. One finger raises it; two fingers lower it. Releasing
                the altitude gesture holds the resulting height.
              </p>
              <span className="metric-status">DEMONSTRATED WITH PHONES</span>
            </article>
            <article>
              <Tag>03 / SHARE</Tag>
              <h3>Let another teammate take over.</h3>
              <p>
                A deliberate fist from another participant transfers control.
                The demo shows two people directing the same virtual drone, with
                the active operator visible to the team.
              </p>
              <span className="metric-status">TWO-PERSON HANDOFF</span>
            </article>
          </div>
          <div className="timing-note">
            <div>
              <span className="mono">
                THE INTERACTION WE ARE BUILDING TOWARD
              </span>
              <h4>Follow the plan. Stay responsive to the people.</h4>
            </div>
            <p>
              The vision is a drone that can act on a planned mission and
              respond when the squad’s intent changes. Today’s phone demo
              establishes a small gesture vocabulary and shared control. Onboard
              gesture recognition, spoken interaction, and physical flight still
              need integration and validation.
            </p>
          </div>
        </section>
        <section className="section wrap" id="positioning">
          <div className="section-heading">
            <div>
              <Tag>02 / SHARED SPATIAL AWARENESS</Tag>
              <h2>
                Teamwork starts with
                <br />
                a sense of where.
              </h2>
            </div>
            <p>
              The interaction needs spatial context.
              <br />
              Radio ranging gives the prototype a relative map.
            </p>
          </div>
          <div className="position-grid">
            <Geometry />
            <div className="position-explainer">
              <div className="technical-label">
                <Radio size={20} />
                <span>ULTRA-WIDEBAND / UWB</span>
              </div>
              <h3>A shared frame of reference.</h3>
              <p>
                Knowing where teammates are gives a command context: who is
                calling, and where are they relative to the group? The prototype
                uses ultra-wideband radio to estimate distances between devices
                without camera images or GPS. Those measurements support the
                shared representation of the squad.
              </p>
              <div className="equation">
                <span>distance ≈ propagation time × c</span>
                <small>Ranging accounts for the reply delay.</small>
              </div>
              <h4>From ranges to a relative map</h4>
              <p>
                Each distance constrains a position to a sphere. Intersections
                explain the geometry; the implementation reconstructs all peers
                together from the complete distance matrix using
                multidimensional scaling.
              </p>
              <p className="fine">
                The illustration uses five range shells to explain an unknown
                point. The five-phone system instead solves 10 mutual distances.
                Global position, orientation, and reflection are not determined
                by distances alone.
              </p>
              <div className="source-row">
                <Source path="ios/SignalMap/RangeGeometry.swift">
                  Geometry implementation
                </Source>
                <a
                  className="source"
                  href="https://www.nxp.com/docs/en/training-presentation/TP-TD24-EUF-AUT-T4768.pdf"
                  target="_blank"
                  rel="noreferrer"
                >
                  UWB reference <ArrowUpRight size={13} />
                </a>
              </div>
            </div>
          </div>
          <div className="math-flow">
            <span>
              PAIR DISTANCES <b>D²</b>
            </span>
            <ChevronRight />
            <span>
              CENTERED GRAM MATRIX <b>−½ JD²J</b>
            </span>
            <ChevronRight />
            <span>
              TOP 3 EIGENVECTORS <b>X = V₃Λ₃½</b>
            </span>
            <ChevronRight />
            <span>
              RELATIVE GROUP SHAPE <Crosshair />
            </span>
          </div>
        </section>
        <section className="network-band">
          <div className="wrap network-inner">
            <div>
              <Tag>COORDINATION WITHIN THE GROUP</Tag>
              <h2>
                A shared picture,
                <br />
                built by the team.
              </h2>
              <p>
                The positioning prototype connects nearby phones over
                authenticated, encrypted peer links. They share measurements and
                build a relative map without installed anchors or a cloud
                positioning service. A separate computer hosts the drone
                simulation.
              </p>
              <p className="fine">
                A deterministically elected peer schedules ranging and shares
                the map. The role can move to another peer; the current
                prototype is peer-coordinated, not leaderless.
              </p>
              <Source path="ios/SignalMap/RoomSession.swift">
                Coordination & recovery
              </Source>
            </div>
            <div
              className="network-diagram"
              aria-label="Local peer network: five phones connected to one another, one elected coordinator, no external service"
            >
              <div className="mesh-top">
                LOCAL PEER NETWORK <span>NO CLOUD DEPENDENCY</span>
              </div>
              <svg
                viewBox="0 0 560 290"
                role="img"
                aria-label="Five interconnected peers; A is the current elected coordinator"
              >
                <g stroke="#4c5c43">
                  {[
                    [85, 150],
                    [240, 60],
                    [450, 100],
                    [405, 235],
                    [200, 235],
                  ].flatMap(([x, y], i, arr) =>
                    arr
                      .slice(i + 1)
                      .map(([xx, yy], j) => (
                        <line key={`${i}${j}`} x1={x} y1={y} x2={xx} y2={yy} />
                      )),
                  )}
                </g>
                {[
                  [85, 150],
                  [240, 60],
                  [450, 100],
                  [405, 235],
                  [200, 235],
                ].map(([x, y], i) => (
                  <g key={i}>
                    <circle
                      cx={x}
                      cy={y}
                      r="23"
                      stroke={i === 0 ? '#b4ef74' : '#77836c'}
                      fill="#141e12"
                    />
                    <text
                      x={x}
                      y={y + 5}
                      textAnchor="middle"
                      fill="#dce9d3"
                      fontFamily="monospace"
                      fontSize="15"
                    >
                      {letters[i]}
                    </text>
                    {i === 0 && (
                      <text x={x - 47} y={y + 43} fill="#b4ef74" fontSize="12">
                        COORDINATOR
                      </text>
                    )}
                  </g>
                ))}
              </svg>
              <div className="mesh-bottom">
                <span>
                  <i className="status-dot" /> UWB · DISTANCE
                </span>
                <span>PEER TRANSPORT · COORDINATION</span>
              </div>
            </div>
          </div>
        </section>
        <section className="section wrap" id="protocol">
          <div className="section-heading">
            <div>
              <Tag>03 / HOW THE PROTOTYPE SHARES A RADIO</Tag>
              <h2>
                One group.
                <br />A coordinated rhythm.
              </h2>
            </div>
            <p>
              The five-peer design shares radio time.
              <br />
              Each pairing adds to the group’s relative map.
            </p>
          </div>
          <RoundRobin />
          <div className="protocol-lifecycle">
            <span>Prepare fresh session</span>
            <ChevronRight />
            <span>Exchange tokens</span>
            <ChevronRight />
            <span>Collect 4 valid readings</span>
            <ChevronRight />
            <span>250 ms grace</span>
            <ChevronRight />
            <span>Release + 300 ms handoff</span>
          </div>
          <div className="section-foot">
            <p>
              Failed attempts back off. Local leases release a stuck radio.
              Incomplete cycles stay out of complete-cycle statistics.
            </p>
            <Source path="ios/SignalMap/RoomTypes.swift">
              Round-robin implementation
            </Source>
          </div>
        </section>
        <section className="section architecture wrap" id="architecture">
          <div className="section-heading">
            <div>
              <Tag>04 / FROM HUMAN INTENT TO ACTION</Tag>
              <h2>
                Know who is calling.
                <br />
                Understand the signal.
              </h2>
            </div>
            <p>
              Spatial context meets an explicit command.
              <br />
              The human directs the simulated response.
            </p>
          </div>
          <div className="system-overview">
            <div className="system-lane">
              <span className="lane-label">
                <Radio size={16} /> POSITION / CAMERA-FREE
              </span>
              <div>
                <b>UWB ranges</b>
                <ChevronRight />
                <b>Relative geometry</b>
                <ChevronRight />
                <b>Position + age</b>
              </div>
            </div>
            <div className="system-lane gesture-lane">
              <span className="lane-label">
                HUMAN INTENT / PROTOTYPE CAMERA
              </span>
              <div>
                <b>MediaPipe</b>
                <ChevronRight />
                <b>Gesture logic</b>
                <ChevronRight />
                <b>Intent + heading</b>
              </div>
            </div>
            <div className="merge-output">
              <span>STATE MERGE</span>
              <ArrowDown size={20} />
              <b>Operator state → command mapping → drone simulator</b>
              <small>
                Today: phone sensors → computer → simulation. Camera frames stay
                on each phone.
              </small>
            </div>
          </div>
          <div className="architecture-detail">
            <div className="custom-training-story">
              <Tag>CUSTOM MACHINE LEARNING</Tag>
              <h3>A gesture vocabulary we can train.</h3>
              <p>
                We built a custom gesture-training pipeline and trained a model
                on our own hand signals, demonstrating that the command
                vocabulary can expand beyond preset gestures.
              </p>
              <p>
                Record examples. Label the signals. Train the model. Connect
                recognized gestures to commands. That makes the vocabulary
                something we can develop around the people using it.
              </p>
              <p className="fine">
                Demonstrated in desktop experiments. The current phone demo uses
                a separate gesture-rule layer.
              </p>
            </div>
            <div className="detail-heading">
              <div>
                <Tag>THE TECHNOLOGY BEHIND THE INTERACTION</Tag>
                <h3>From a visible gesture to a deliberate command.</h3>
              </div>
              <span className="mono">FIG. 04 / MODEL ARCHITECTURE</span>
            </div>
            <Tabs defaultValue="inference">
              <TabsList className="pipeline-tabs">
                <TabsTrigger value="inference">Runtime inference</TabsTrigger>
                <TabsTrigger value="training">Custom training</TabsTrigger>
              </TabsList>
              <TabsContent value="inference">
                <Pipeline training={false} />
              </TabsContent>
              <TabsContent value="training">
                <Pipeline training />
              </TabsContent>
            </Tabs>
            <div className="source-row">
              <Source path="ios/SignalMap/GestureCamera.swift">
                iOS inference
              </Source>
              <Source path="scripts/train_gestures.py">Desktop training</Source>
              <a
                className="source"
                href="https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer"
                target="_blank"
                rel="noreferrer"
              >
                MediaPipe documentation <ArrowUpRight size={13} />
              </a>
            </div>
          </div>
        </section>
        <section className="section performance wrap" id="performance">
          <div className="section-heading">
            <div>
              <Tag>05 / THE PATH TO A DRONE TEAMMATE</Tag>
              <h2>
                A working prototype.
                <br />
                A testable path.
              </h2>
            </div>
            <p>
              Demonstrate the interaction.
              <br />
              Measure it, then bring it to hardware.
            </p>
          </div>
          <div className="engineering-notes roadmap">
            <div>
              <h4>Measure the interaction</h4>
              <p>
                Quantify recognition errors, response time, and unintended
                commands. Test visibility changes and competing operators beyond
                the recorded two-person demonstration.
              </p>
            </div>
            <div>
              <h4>Bring the sensing onboard</h4>
              <p>
                Integrate onboard compute and a camera with a physical drone.
                Phone-based sensing is our test platform; recognition from a
                moving aircraft and physical flight are not yet validated.
                GPS-independent navigation remains a separate requirement.
              </p>
            </div>
            <div>
              <h4>Add another way to communicate</h4>
              <p>
                Spoken commands are in development. The longer-term goal is to
                combine gestures and speech so people can express intent in the
                way that fits the moment.
              </p>
            </div>
          </div>
          <div className="detail-heading">
            <div>
              <Tag>UNDER THE HOOD / POSITIONING PROTOTYPE</Tag>
              <h3>Targets and measurements stay distinct.</h3>
            </div>
          </div>
          <div className="metric-cards">
            <article className="target-card">
              <Tag>01 / DESIGN TARGET</Tag>
              <strong>
                &lt; 5<span>s</span>
              </strong>
              <h3>Complete ranging cycle</h3>
              <p>
                Target for all 10 pair distances across a five-phone group.
                Device setup, radio conditions, and retries determine actual
                cycle time.
              </p>
              <span className="metric-status">HARDWARE BENCHMARK PENDING</span>
            </article>
            <article>
              <Tag>02 / PROTOCOL CONSTANT</Tag>
              <strong>4</strong>
              <h3>Valid readings per attempt</h3>
              <p>
                The completion milestone before peer grace and teardown. Four
                samples are a collection threshold, not an accuracy guarantee.
              </p>
              <span className="metric-status">IMPLEMENTED</span>
            </article>
            <article>
              <Tag>03 / FAILURE BOUND</Tag>
              <strong>
                4<span>s</span>
              </strong>
              <h3>Measurement deadline</h3>
              <p>
                Default per-attempt measurement window. A separate local lease
                adds five seconds to release the radio if coordination stalls.
              </p>
              <span className="metric-status">IMPLEMENTED</span>
            </article>
          </div>
          <div className="timing-note">
            <div>
              <span className="mono">WHAT A “TICK” MEANS</span>
              <h4>A complete cycle is a fresh map.</h4>
            </div>
            <p>
              The coordinator checks work every 100 ms. Bridge packets arrive at
              roughly 10 Hz. Neither means a new position was measured: fresh
              geometry requires a complete set of ranges. Profiling reports
              complete-cycle p50/p95, fit residual, and measurement age.
            </p>
          </div>
          <div className="engineering-notes">
            <div>
              <h4>Relative, not geographic</h4>
              <p>
                No latitude, longitude, or gravity-aligned height. Nearly planar
                layouts leave the third axis uncertain.
              </p>
            </div>
            <div>
              <h4>Freshness is explicit</h4>
              <p>
                Motion during a cycle can deform the estimate. The prototype
                expires positions after eight seconds.
              </p>
            </div>
            <div>
              <h4>Evidence stays inspectable</h4>
              <p>
                Per-attempt timing, failures, and full-cycle statistics export
                as JSONL. Field navigation performance is not yet validated.
              </p>
            </div>
          </div>
          <Source path="ios/README.md">
            Read protocol details & profiling methodology
          </Source>
        </section>
        <footer className="wrap">
          <div>
            <a className="brand" href="#main">
              <Mark />
              TactLink
            </a>
            <p>One squad. Shared drone support.</p>
          </div>
          <a
            className="repo-link"
            href="https://github.com/yav-fi/tactlink"
            target="_blank"
            rel="noreferrer"
          >
            <GitBranch size={18} /> Explore the implementation{' '}
            <MoveUpRight size={18} />
          </a>
          <div className="footer-bottom">
            <span>HACKATHON PROTOTYPE / 2026</span>
            <span>PLAN TOGETHER. SIGNAL INTENT. SHARE CONTROL.</span>
          </div>
        </footer>
      </main>
    </>
  );
}
