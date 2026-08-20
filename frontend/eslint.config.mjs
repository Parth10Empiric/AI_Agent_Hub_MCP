import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      "node_modules/**",
      // The glob covers ".next" AND ".next-e2e", the separate build
      // directory the end-to-end tests use. Without the wildcard,
      // linting walks tens of thousands of generated files and reports
      // thousands of "problems" in code nobody wrote.
      ".next*/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },
];

export default eslintConfig;
