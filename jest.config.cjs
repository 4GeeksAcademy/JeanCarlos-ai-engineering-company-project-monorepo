module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/tests"],
  testMatch: ["**/*.test.ts"],
  collectCoverageFrom: ["src/utils/*.ts"],
  coverageDirectory: "coverage",
  coverageReporters: ["text", "lcov"],
};
