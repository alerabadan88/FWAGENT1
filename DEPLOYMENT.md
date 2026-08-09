# Deploying

Everything needed is in the repository. What follows is what each host costs
you in behaviour, because the differences are not cosmetic.

## What the app needs from a host

| | Why | If absent |
|---|---|---|
| A filesystem that persists | sessions and the corpus | resume breaks; the corpus collects nothing |
| A Zephyr checkout (`ZEPHYR_BASE`) | peripheral counts, binding properties | those checks abstain — see below |
| Outbound HTTPS | fetching bindings from a pinned tag | binding verification is skipped |

The app reports both at `/api/health` and shows a banner when either is
missing. It never pretends to save something it is discarding.

## A public URL right now, with nothing given up

`cloudflared` publishes the local instance, which already has a real disk and
the full Zephyr checkout:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints a `*.trycloudflare.com` URL. No account, no signup. Sessions persist
and every check runs, because it is your machine serving it.

Two things to know: anyone with the link can use it, and it stops when you
close the terminal. For a permanent address use a container host below.

## Vercel

Works, with two real losses. Deploy with:

```bash
npm i -g vercel
vercel login          # interactive, opens a browser
vercel --prod
```

`vercel.json` and `api/index.py` are already here.

**Sessions and the corpus will not persist.** Vercel's Python runtime is
serverless: each request gets its own temporary filesystem. Writes appear to
succeed and the data is gone by the next request, which is the worst possible
failure mode for a corpus — it looks like it is collecting and it is not. The
app detects `VERCEL` in the environment and says so in the UI.

To get persistence back, replace the file store in `webapp/store.py` with
Vercel Postgres or Upstash Redis. The module is small and has one job, so this
is a contained change rather than a rewrite.

**Peripheral counts will abstain** unless you ship the devicetree subset. The
app only reads `dts/` -- 23 MB, not the 6.4 GB west workspace -- so a sparse
checkout does fit inside the function limit. Without it the "which USART is the
console wired to" question stops being asked and the contention check goes
quiet. Both abstain rather than guess, which is correct behaviour and still a
loss. The `Dockerfile` shows the sparse checkout; the same two git commands
work in a Vercel build step.

Binding resolution keeps working — those are fetched from `zephyr@v4.4.2` over
HTTPS and cached.

**Use Vercel if** you want a public link for someone to click through the flow.
Do not use it as the real instance.

## A container host — the fit for this

Render, Railway, Fly.io: a long-running process with a mounted disk. Sessions
persist, the corpus accumulates, and you can mount a Zephyr checkout.

`Dockerfile` and `render.yaml` are here.

```bash
docker build -t fw-automation-agent .
docker run -p 8000:8000 -v fwagent-data:/data fw-automation-agent
```

With a Zephyr checkout, which restores the checks Vercel loses:

```bash
docker run -p 8000:8000 \
  -v fwagent-data:/data \
  -v /path/to/zephyrproject/zephyr:/zephyr:ro \
  -e ZEPHYR_BASE=/zephyr \
  fw-automation-agent
```

On Render, `render.yaml` declares a 1 GB disk at `/data`. The free tier spins
down when idle; the disk survives that.

## Compiling on the server

`POST /api/sessions/{id}/build` runs `west build` and puts `zephyr.hex`,
`zephyr.bin` and `zephyr.elf` in the zip. That needs more than the devicetree
subset:

| | Size |
|---|---|
| `dts/` sparse checkout | 23 MB |
| Zephyr SDK, arm-zephyr-eabi only | ~1 GB |
| west, cmake, ninja | from PyPI, no admin needed |

`/api/build-capability` reports whether a host can compile and lists exactly
what it lacks. An instance without the toolchain still generates the port and
says so in the UI — the user builds locally. Nothing is silently degraded.

The `Dockerfile` ships the devicetree subset but **not** the SDK, because a
1 GB layer is a decision to make deliberately. To build server-side, add:

```dockerfile
RUN pip install west && west sdk install --toolchains arm-zephyr-eabi
```

## Optional: the free-text step

`POST /api/describe` is the only endpoint that uses a model. Set
`ANTHROPIC_API_KEY` to enable it. Without it that endpoint returns 503 and
everything else works — the questions are enumerated in ordinary code, so the
product does not depend on a model being reachable or paid for.

## What no host gives you

`west build` does not run in any of these. Generating a devicetree is not
compiling one, and the deployed app says so on every download. Building is a
local step, and the only thing that turns "structurally correct" into
"correct".

Closing that loop — running the build from inside the flow and recording the
result in the corpus — is the highest-value thing left, because that result is
the only supervision signal in the system.
