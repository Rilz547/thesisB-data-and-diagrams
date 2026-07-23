import { BarChart, Callout, Card, CardBody, CardHeader, Grid, H1, H2, H3, Stack, Stat, Table, Text } from "cursor/canvas";

// CPU-beam vs GPU-beam baseline: Bonson openfish gpu_scan + cpu_beam, on Jetson (Tegra, aarch64).
// Defaults: -C 128 -c 12288 -K 4096 -p 150. 1 warmup + 1 timed run per config×mode.

type Row = {
  config: string;
  baseline: number;
  cpuBeam: number;
  delta: number;
  slowdown: number;
  pctSlower: number;
  infer: number;
  gpuDecode: number;
  cpuBeamCpuTime: number;
  beamVsDecode: number; // inferred cpu-beam time / gpu decode time
};

const rows: Row[] = [
  { config: "FAST 1k",  baseline: 11.006,  cpuBeam: 12.022,  delta: 1.016,  slowdown: 1.09, pctSlower: 9.2,  infer: 6.648,  gpuDecode: 3.395,  cpuBeamCpuTime: 41.587,  beamVsDecode: 3.5 },
  { config: "HAC 1k",   baseline: 45.722,  cpuBeam: 52.387,  delta: 6.665,  slowdown: 1.15, pctSlower: 14.6, infer: 40.493, gpuDecode: 4.187,  cpuBeamCpuTime: 80.200,  beamVsDecode: 12.5 },
  { config: "FAST 20k", baseline: 197.159, cpuBeam: 218.971, delta: 21.812, slowdown: 1.11, pctSlower: 11.1, infer: 118.555, gpuDecode: 67.041, cpuBeamCpuTime: 806.598, beamVsDecode: 3.3 },
  { config: "HAC 20k",  baseline: 889.712,  cpuBeam: 1020.086, delta: 130.374, slowdown: 1.15, pctSlower: 14.7, infer: 792.174, gpuDecode: 83.460, cpuBeamCpuTime: 1588.081, beamVsDecode: 12.2 },
];

const configs = rows.map((r) => r.config);

export default function CpuBeamBenchmarkReport() {
  return (
    <Stack gap={20}>
      <H1>CPU-beam vs GPU-beam: Jetson benchmark</H1>
      <Text tone="secondary" size="small">
        Bonson openfish gpu_scan + cpu_beam (streaming pipeline) vs regular fused GPU decode, on a
        Jetson (Tegra, aarch64) host. Defaults: <Text weight="semibold">-C 128 -c 12288 -K 4096 -p 150</Text>;
        1 warmup + 1 timed run per config×mode; output to /dev/null.
      </Text>

      <Grid columns={4} gap={12}>
        <Stat value="1.09-1.15x" label="Slowdown vs GPU" />
        <Stat value="+9.2%" label="Best overhead (FAST 1k)" tone="success" />
        <Stat value="+14.7%" label="Worst overhead (HAC 20k)" tone="warning" />
        <Stat value="≈ GPU" label="Verdict (within 15%)" tone="success" />
      </Grid>

      <Callout tone="success" title="Verdict">
        CPU-beam lands at 1.09-1.15x of GPU-only decode, a large improvement over the earlier
        2.5-3.3x attempt. Bonson&apos;s design works on Jetson: the CPU beam is mostly hidden
        behind GPU inference, so the exposed overhead stays in a 9-15% band.
      </Callout>

      <H2>Headline comparison (timed real time)</H2>
      <Table
        headers={["Config", "Baseline (s)", "CPU-beam (s)", "Delta (s)", "Slowdown", "% slower"]}
        rows={rows.map((r) => [
          r.config,
          r.baseline.toFixed(3),
          r.cpuBeam.toFixed(3),
          `+${r.delta.toFixed(3)}`,
          `${r.slowdown.toFixed(2)}x`,
          `+${r.pctSlower.toFixed(1)}%`,
        ])}
        columnAlign={["left", "right", "right", "right", "right", "right"]}
        rowTone={["success", "warning", "neutral", "warning"]}
        striped
      />

      <H2>Where the overhead comes from</H2>
      <Text tone="secondary" size="small">
        The streaming (cpu_beam) path only logs a summary line, so decode/inference split is
        inferred from the baseline breakdown + cpu_beam wall/CPU time. &quot;CPU-beam time&quot;
        below is the inferred decode-stage wall time (the bottleneck stage); &quot;beam vs GPU
        decode&quot; is that divided by the baseline GPU decode time.
      </Text>
      <Table
        headers={[
          "Config",
          "GPU infer (s)",
          "GPU decode (s)",
          "CPU-beam real (s)",
          "CPU-beam CPU time (s)",
          "Beam vs GPU decode",
        ]}
        rows={rows.map((r) => [
          r.config,
          r.infer.toFixed(1),
          r.gpuDecode.toFixed(1),
          r.cpuBeam.toFixed(1),
          r.cpuBeamCpuTime.toFixed(0),
          `${r.beamVsDecode.toFixed(1)}x`,
        ])}
        columnAlign={["left", "right", "right", "right", "right", "right"]}
        striped
      />

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Wall-clock overhead vs GPU</H3>
          <BarChart
            categories={configs}
            series={[{ name: "% slower vs GPU", data: rows.map((r) => r.pctSlower), tone: "warning" }]}
            valueSuffix="%"
            referenceLines={[{ value: 15, label: "15% line", tone: "neutral" }]}
          />
          <Text tone="tertiary" size="small">
            Source: cpu_beam_test.py timed runs · lower is better.
          </Text>
        </Stack>
        <Stack gap={8}>
          <H3>CPU beam vs GPU beam (the beam itself)</H3>
          <BarChart
            categories={configs}
            series={[{ name: "CPU-beam / GPU decode", data: rows.map((r) => r.beamVsDecode), tone: "info" }]}
            valueSuffix="x"
            referenceLines={[{ value: 1, label: "parity", tone: "success" }]}
          />
          <Text tone="tertiary" size="small">
            Inferred decode-stage time divided by baseline GPU decode time. HAC&apos;s beam is
            ~12x the GPU beam; FAST ~3.3x.
          </Text>
        </Stack>
      </Grid>

      <Card>
        <CardHeader>Findings</CardHeader>
        <CardBody>
          <Stack gap={10}>
            <Text>
              <Text weight="semibold">1. The overhead is the CPU beam compute, not the D2H copy.</Text>{" "}
              CPU time explodes for cpu_beam (FAST 20k: 40s→807s; HAC 20k: 706s→1588s); all cores
              pegged on beam search. The Jetson D2H copy of bwd/post + int8 scores is cheap shared-DRAM.
            </Text>
            <Text>
              <Text weight="semibold">2. The CPU beam is the bottleneck stage in every config.</Text>{" "}
              Inferred beam time (≈ cpu_beam wall) exceeds GPU inference in all four cases, so it is
              not fully hidden, but overlap with inference still absorbs the bulk (FAST ~118s hidden,
              HAC ~792s hidden), leaving only 9-15% exposed.
            </Text>
            <Text>
              <Text weight="semibold">3. HAC overhead (15%) &gt; FAST (11%) because HAC&apos;s beam is heavier.</Text>{" "}
              HAC&apos;s CPU beam is ~12x the GPU beam vs ~3.3x for FAST. Even on Jetson&apos;s slow GPU
              (HAC inference 792s) the HAC beam (≈1020s) still exceeds inference, so it can&apos;t
              fully hide.
            </Text>
            <Text>
              <Text weight="semibold">4. RAM is fine / lower</Text> for cpu_beam (FAST 20k: 4.49→2.78 GB;
              HAC 20k: 5.63→5.46 GB); the streaming pipeline holds less in memory at once.
            </Text>
          </Stack>
        </CardBody>
      </Card>

      <Callout tone="info" title="Jetson caveat">
        On a discrete GPU (Bonson&apos;s host) the bwd/post + int8 scores are read zero-copy from
        managed memory while the GPU infers the next batch. Jetson has{" "}
        <Text weight="semibold">ConcurrentManagedAccess = 0</Text>, so the CPU can&apos;t deref
        GPU-written managed pages while the GPU is busy; this run D2H-copies them to plain host
        buffers on the scan stream first. That copy is the Jetson tax; the beam compute is the
        real bottleneck. Lever to close the HAC gap: the beam itself (more/faster CPU cores,
        beam kernel tuning), not the memory path.
      </Callout>
    </Stack>
  );
}
