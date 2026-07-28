import { BarChart, Callout, Card, CardBody, CardHeader, Grid, H1, H2, H3, LineChart, Stack, Stat, Table, Text } from "cursor/canvas";

// Load-imbalance investigation: GPU chunk padding in partial/tail batches.
// FAST 1k reads, Jetson (Tegra, aarch64), overlap-decode=yes, -c 12288 -K 4096 -p 150.
// "narrow" = right-size GPU launch to N real chunks (torch::Tensor::narrow).
// "fixedc" = original fixed-C launch, pads (C-N) dummy slots (baseline waste).

type FlushRow = {
  flush: number; padded: number; fixedc: number; narrow: number;
  speedup: number; nInfer: number; nDecode: number;
};

const flush: FlushRow[] = [
  { flush: 128, padded: 0.4,  fixedc: 10.123, narrow: 10.365, speedup: 0.98, nInfer: 5.163, nDecode: 8.597 },
  { flush: 96,  padded: 26.2, fixedc: 12.884, narrow: 10.973, speedup: 1.17, nInfer: 6.017, nDecode: 9.314 },
  { flush: 64,  padded: 50.2, fixedc: 17.922, narrow: 9.752,  speedup: 1.84, nInfer: 4.337, nDecode: 8.254 },
  { flush: 48,  padded: 62.8, fixedc: 23.159, narrow: 10.427, speedup: 2.22, nInfer: 3.993, nDecode: 8.994 },
  { flush: 32,  padded: 75.1, fixedc: 33.528, narrow: 11.914, speedup: 2.81, nInfer: 2.694, nDecode: 10.414 },
  { flush: 24,  padded: 81.3, fixedc: 43.773, narrow: 14.216, speedup: 3.08, nInfer: 2.434, nDecode: 12.758 },
  { flush: 16,  padded: 87.5, fixedc: 64.469, narrow: 17.580, speedup: 3.67, nInfer: 2.651, nDecode: 16.103 },
  { flush: 12,  padded: 90.6, fixedc: 85.088, narrow: 22.534, speedup: 3.78, nInfer: 3.260, nDecode: 21.021 },
  { flush: 8,   padded: 93.8, fixedc: 126.696,narrow: 30.866, speedup: 4.10, nInfer: 4.701, nDecode: 29.357 },
  { flush: 4,   padded: 96.9, fixedc: 250.788,narrow: 56.965, speedup: 4.40, nInfer: 8.641, nDecode: 55.405 },
];

const flushCats = flush.map((r) => String(r.flush));

type Cell = { c: number; ft: string; fixedc: number; narrow: number; sp: number };
const cells: Cell[] = [
  { c: 64,  ft: "full", fixedc: 9.752,  narrow: 9.844,  sp: 0.99 },
  { c: 64,  ft: "32",   fixedc: 17.426, narrow: 11.865, sp: 1.47 },
  { c: 64,  ft: "8",    fixedc: 63.846, narrow: 30.874, sp: 2.07 },
  { c: 64,  ft: "4",    fixedc: 125.420,narrow: 56.959, sp: 2.20 },
  { c: 128, ft: "full", fixedc: 10.117, narrow: 10.253, sp: 0.99 },
  { c: 128, ft: "32",   fixedc: 33.531, narrow: 11.887, sp: 2.82 },
  { c: 128, ft: "8",    fixedc: 126.681,narrow: 30.872, sp: 4.10 },
  { c: 128, ft: "4",    fixedc: 250.857,narrow: 56.964, sp: 4.40 },
];

export default function LoadImbalanceReport() {
  return (
    <Stack gap={20}>
      <H1>Load imbalance: GPU chunk padding in partial batches</H1>
      <Text tone="secondary" size="small">
        Streaming / low-latency framing. When fewer than C chunks are queued, the original
        fixed-C GPU launch pads the remaining slots with zeros and wastes inference + decode on
        them. The &quot;narrow&quot; fix right-sizes each launch to the N real chunks via
        torch::Tensor::narrow. FAST 1k reads, Jetson, overlap-decode=yes, 1 timed run per cell.
      </Text>

      <Grid columns={4} gap={12}>
        <Stat value="9.75s" label="Best FAST 1k (flush=64 narrow)" tone="success" />
        <Stat value="4.40x" label="Speedup at flush=4 (C=128)" tone="success" />
        <Stat value="1.90x" label="FAST 20k flush=64 vs fixedc" tone="info" />
        <Stat value="1.78x" label="HAC 1k flush=64 vs fixedc" tone="info" />
      </Grid>

      <Callout tone="success" title="Verdict">
        The narrow fix removes the padding tax entirely. On FAST 1k the sweet spot is flush=64
        (9.75s), faster than packing full C=128. At full batches it is free (0.98x); at low flush
        it recovers up to 4.40x. FAST 20k and HAC 1k spot-checks confirm Layer 1 at scale and on
        a heavier model. Residual cost is Layer 2 (launch overhead / low occupancy).
      </Callout>

      <H2>1. Flush sweep (C=128): real time vs flush threshold</H2>
      <LineChart
        categories={flushCats}
        series={[
          { name: "fixed-C (padded, baseline)", data: flush.map((r) => r.fixedc), tone: "danger" },
          { name: "narrow (right-sized)", data: flush.map((r) => r.narrow), tone: "success" },
        ]}
        valueSuffix="s"
        height={260}
      />
      <Text tone="tertiary" size="small">
        x-axis flush threshold (chunks per GPU batch); lower = more streaming-like. fixed-C
        blows up as padding (C-N)/C rises; narrow stays bounded. Source: flush_sweep_test.py.
      </Text>

      <Table
        headers={["flush", "padded %", "fixed-C (s)", "narrow (s)", "speedup", "narrow infer (s)", "narrow decode (s)"]}
        rows={flush.map((r) => [
          String(r.flush), `${r.padded.toFixed(1)}%`, r.fixedc.toFixed(2), r.narrow.toFixed(2),
          `${r.speedup.toFixed(2)}x`, r.nInfer.toFixed(2), r.nDecode.toFixed(2),
        ])}
        columnAlign={["right", "right", "right", "right", "right", "right", "right"]}
        striped
      />

      <H2>2. Batch-width sweep: does narrow&apos;s win grow with C?</H2>
      <Text tone="secondary" size="small">
        padded % = (C-N)/C. For a fixed flush N, a larger C means MORE padding, so the fixed-C
        baseline wastes more and narrow removes more. C=256 OOMs on this Jetson with
        overlap-decode (the ~1 GB contiguous bwd/post + LSTM-workspace allocation fails), so the
        grid stops at C=128.
      </Text>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Speedup (fixed-C / narrow) by C</H3>
          <BarChart
            categories={["full", "32", "8", "4"]}
            series={[
              { name: "C=64", data: cells.filter((c) => c.c === 64).map((c) => c.sp), tone: "info" },
              { name: "C=128", data: cells.filter((c) => c.c === 128).map((c) => c.sp), tone: "success" },
            ]}
            valueSuffix="x"
            referenceLines={[{ value: 1, label: "parity", tone: "neutral" }]}
          />
          <Text tone="tertiary" size="small">
            At flush=4 the speedup doubles from 2.20x (C=64) to 4.40x (C=128). Source: batchwidth_sweep_test.py.
          </Text>
        </Stack>
        <Stack gap={8}>
          <H3>narrow absolute time is flat across C</H3>
          <BarChart
            categories={["full", "32", "8", "4"]}
            series={[
              { name: "C=64 narrow", data: cells.filter((c) => c.c === 64).map((c) => c.narrow), tone: "info" },
              { name: "C=128 narrow", data: cells.filter((c) => c.c === 128).map((c) => c.narrow), tone: "success" },
            ]}
            valueSuffix="s"
          />
          <Text tone="tertiary" size="small">
            flush=4: 57.0s at C=64 AND C=128; flush=8: 30.9s at both. narrow decouples throughput
            from batch width. fixed-C doubles (125s to 251s at flush=4).
          </Text>
        </Stack>
      </Grid>

      <Table
        headers={["C", "flush", "fixed-C (s)", "narrow (s)", "speedup"]}
        rows={cells.map((c) => [
          String(c.c), c.ft, c.fixedc.toFixed(2), c.narrow.toFixed(2), `${c.sp.toFixed(2)}x`,
        ])}
        columnAlign={["right", "right", "right", "right", "right"]}
        striped
      />

      <H2>3. Two layers of cost</H2>
      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>Layer 1: padding tax (removed by narrow)</CardHeader>
          <CardBody>
            <Stack gap={8}>
              <Text>
                The fixed-C launch always runs C slots; a tail batch with N real chunks infers and
                decodes the other (C-N) dummy slots then discards them. Waste = (C-N)/C of every
                GPU slot.
              </Text>
              <Text>
                This is the gap between fixed-C and narrow. It grows with padded %: 0.4 % at
                flush=128 (negligible) to 96.9 % at flush=4, where fixed-C spends 96.9 % of GPU
                work on padding. narrow eliminates it by narrowing the input tensor to N before
                forward.
              </Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Layer 2: launch overhead + low occupancy (residual)</CardHeader>
          <CardBody>
            <Stack gap={8}>
              <Text>
                Even after narrow, time still grows as flush drops: 10.4 s at flush=128 to 57.0 s
                at flush=4 (5.5x). This is the irreducible cost of many small batches: more kernel
                launches per chunk and low GPU occupancy when N is small.
              </Text>
              <Text>
                narrow infer time actually falls then rises (5.16 s to 2.43 s to 8.64 s): fewer
                slots per launch cut compute, but past flush=32 the launch count dominates. narrow
                decode overtakes narrow infer below flush=16 as per-batch fixed decode cost
                amortises over fewer chunks.
              </Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <H2>4. Spot-checks: FAST 20k and HAC 1k</H2>
      <Text tone="secondary" size="small">
        Same flags as the main grids (-C 128, overlap on). Confirms the flush=64 sweet spot at
        scale and that Layer 1 is not FAST-only.
      </Text>
      <Table
        headers={["Config", "flush", "mode", "real (s)", "padded %", "vs fixedc"]}
        rows={[
          ["FAST 20k", "128", "narrow", "176.410", "0.3%", "—"],
          ["FAST 20k", "64", "narrow", "175.291", "50.1%", "1.90x"],
          ["FAST 20k", "64", "fixedc", "332.967", "50.1%", "—"],
          ["HAC 1k", "128", "narrow", "45.582", "0.4%", "~1.00x"],
          ["HAC 1k", "128", "fixedc", "45.350", "0.4%", "—"],
          ["HAC 1k", "64", "narrow", "49.275", "50.2%", "1.78x"],
          ["HAC 1k", "64", "fixedc", "87.791", "50.2%", "—"],
        ]}
        columnAlign={["left", "right", "left", "right", "right", "right"]}
        striped
      />

      <Card>
        <CardHeader>Findings</CardHeader>
        <CardBody>
          <Stack gap={10}>
            <Text>
              <Text weight="semibold">1. narrow is free at full batch.</Text> At flush=C, only the
              final tail is partial (padded 0.4 %), so fixed-C and narrow match within noise
              (0.98-0.99x). The fix can ship on by default with no offline-throughput penalty.
            </Text>
            <Text>
              <Text weight="semibold">2. flush=64 is the FAST sweet spot.</Text> On FAST 1k
              (9.75s) and FAST 20k (175.3s) it beats packing full C=128, while still cutting half
              the padding under fixed-C.
            </Text>
            <Text>
              <Text weight="semibold">3. The padding tax dominates streaming latency.</Text> At
              flush=4, fixed-C is 250.8 s vs narrow 57.0 s, a 4.40x gap.
            </Text>
            <Text>
              <Text weight="semibold">4. narrow&apos;s win grows with batch width C.</Text> At
              flush=4 the speedup doubles from 2.20x (C=64) to 4.40x (C=128); absolute narrow time
              stays flat across C.
            </Text>
            <Text>
              <Text weight="semibold">5. Effect is not FAST-only.</Text> HAC 1k at flush=64 is
              still 1.78x vs fixedc. HAC pays a bit more Layer 2 at flush=64 than FAST (infer-heavy).
            </Text>
            <Text>
              <Text weight="semibold">6. Layer 2 is the remaining lever.</Text> After narrow,
              flush=4 still costs 57 s vs 10 s at full. Closing it needs fewer, fuller launches
              (host-side coalescing), not more GPU work.
            </Text>
          </Stack>
        </CardBody>
      </Card>

      <Callout tone="info" title="Memory ceiling">
        C=256 with overlap-decode OOMs on this Jetson: a single ~1.08 GB contiguous allocation
        (cuDNN LSTM workspace / bwd-post buffer sized for C=256) fails with NvMap error 12. The
        2D grid therefore stops at C=128. The C-interaction trend (2.20x to 4.40x as C doubles)
        is already conclusive without C=256.
      </Callout>
    </Stack>
  );
}
