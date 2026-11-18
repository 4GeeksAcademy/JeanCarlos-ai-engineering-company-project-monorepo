/**
 * Unit tests for the backoffice utilities (FE-019 / AUTH-088).
 * Run from uis/backoffice: `npm test` or `npx jest --coverage`.
 * @type {import("jest").Config}
 */
module.exports = {
  testEnvironment: "node", // fetch, Request, Headers and crypto come from Node itself
  roots: ["<rootDir>/__tests__"],
  testMatch: ["**/*.test.ts"],
  transform: {
    "^.+\\.tsx?$": ["ts-jest", {
      isolatedModules: true,
      tsconfig: { jsx: "react-jsx", module: "commonjs", esModuleInterop: true, resolveJsonModule: true },
    }],
  },
  moduleNameMapper: { "^@/(.*)$": "<rootDir>/src/$1" },
  clearMocks: true,
  restoreMocks: true,
  collectCoverageFrom: [
    "src/lib/auth.ts",
    "src/lib/server-api.ts",
    "src/lib/suppliers.ts",
    "src/lib/inventory.ts",
  ],
  coverageReporters: ["text", "text-summary"],
};
