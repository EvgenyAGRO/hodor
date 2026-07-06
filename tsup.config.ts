import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/cli.ts", "src/cli-main.ts", "src/index.ts"],
  format: ["esm"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "node22",
});
