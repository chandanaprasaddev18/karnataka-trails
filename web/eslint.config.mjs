import next from "eslint-config-next";

/**
 * Lint config with one job that matters here: catching stale closures.
 *
 * `react-hooks/exhaustive-deps` is a WARNING by default. It is an error in this
 * project because a missing dependency already shipped a wrong feature: the plan
 * wizard's submit callback omitted `district` from its deps, so it captured the
 * value the page loaded with. The user picked Mysuru, the card showed as selected,
 * and the request said Chikkamagaluru — invisible to TypeScript, invisible in the
 * DOM, and only detectable by reading the itinerary at the end.
 */
export default [
  ...next,
  {
    rules: {
      "react-hooks/exhaustive-deps": "error",
    },
  },
];
