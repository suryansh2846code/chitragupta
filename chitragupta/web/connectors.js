/**
 * Adding a source, and setting one up.
 *
 * The per-connector setup help, the custom-app form, the connector catalog
 * browser, and the permission screen shown before an MCP server is added.
 *
 * **Consent to something nobody has been shown is not consent.** A connector's
 * tools are read from the server and displayed *before* it is added
 * (`connectorPermissions`), because "add" is the only moment the user is asked,
 * and a list they never saw is not a decision they made.
 *
 * Setup instructions are per-source prose, not a generic form. Every field a
 * user types lands in the chmod-600 secrets store through
 * `POST /api/connectors/{name}/secret` — never in a file in the repo, and never
 * asked for at a terminal.
 */

// ── what each source looks like, and where it belongs ──────────────────────
// The marks are drawn here rather than fetched: every asset a page pulls from
// a vendor's CDN is a request that says which app the user is running, and the
// whole product promise is that nothing leaves the machine. They are simple
// recognisable shapes in each brand's colour, not pixel copies.
//
// Keyed by connector name from REGISTRY, so a connector without an entry still
// renders — it falls back to a neutral mark and its own group. Adding a
// connector never needs an edit here to keep working.
const CONNECTOR_ICONS = {
  gmail: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#fff" d="M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>
    <path fill="#EA4335" d="M3 6.9 12 13l9-6.1v2.3L12 15.4 3 9.2z"/></svg>`,
  gcal: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4.5" width="18" height="16" rx="2.5" fill="#fff"/>
    <rect x="3" y="4.5" width="18" height="4" rx="2.5" fill="#4285F4"/>
    <text x="12" y="17" font-size="8.5" font-weight="700" text-anchor="middle" fill="#4285F4" font-family="Helvetica,Arial">31</text></svg>`,
  gdrive: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#0F9D58" d="m8.5 3.5 7 0 4.5 8-3.5 0z"/>
    <path fill="#F4B400" d="m20 11.5-3.5 6-7 0 3.5-6z"/>
    <path fill="#4285F4" d="M8.5 3.5 4 11.5l3.5 6 3.5-6z"/></svg>`,
  notion: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="3.5" fill="#fff"/>
    <path fill="#111" d="M8 8.2h1.9l4 5.6V8.2h1.6v7.6h-1.8l-4.1-5.8v5.8H8z"/></svg>`,
  github: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#e6edf3" d="M12 2.2a9.8 9.8 0 0 0-3.1 19.1c.5.1.7-.2.7-.5v-1.8c-2.7.6-3.3-1.3-3.3-1.3-.5-1.1-1.1-1.4-1.1-1.4-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.3 1.1 2.9.8.1-.6.3-1.1.6-1.3-2.2-.3-4.5-1.1-4.5-4.9 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.7 1a9.4 9.4 0 0 1 5 0c1.9-1.3 2.7-1 2.7-1 .5 1.4.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.8-2.3 4.6-4.5 4.9.4.3.7.9.7 1.9v2.8c0 .3.2.6.7.5A9.8 9.8 0 0 0 12 2.2"/></svg>`,
  linear: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="4.5" fill="#5E6AD2"/>
    <path fill="#fff" d="M7 13.4 10.6 17a5.6 5.6 0 0 1-3.6-3.6m-.3-2.1 5.9 5.9q.8-.1 1.5-.4L7.1 9.8q-.3.7-.4 1.5m.9-2.8 7.6 7.6q.5-.4.9-.9L8.5 7.6q-.5.4-.9.9m2-1.4 7.1 7.1A5.7 5.7 0 0 0 9.6 7.1"/></svg>`,
  imessage: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="2.5" y="2.5" width="19" height="19" rx="5" fill="#34C759"/>
    <path fill="#fff" d="M12 6.4c-3.4 0-6.1 2.2-6.1 5s2.7 5 6.1 5q.8 0 1.5-.2l2.7 1.3-.7-2.3c1.6-.9 2.6-2.3 2.6-3.8 0-2.8-2.7-5-6.1-5"/></svg>`,
  apple_mail: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="2.5" y="4.5" width="19" height="15" rx="4" fill="#1F8DFB"/>
    <path fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round" d="m5.5 8.5 6.5 5 6.5-5"/></svg>`,
  apple_calendar: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4.5" width="18" height="16" rx="3.5" fill="#fff"/>
    <rect x="3" y="4.5" width="18" height="4.5" rx="3.5" fill="#FF3B30"/>
    <text x="12" y="17.5" font-size="8.5" font-weight="600" text-anchor="middle" fill="#1c1c1e" font-family="Helvetica,Arial">17</text></svg>`,
  files: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#54A0FF" d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  // Drawn rather than taken from the icon set: that publishes Apple's logo,
  // and Apple Health is a heart. The vendor's mark is not the app's.
  apple_health: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="#FF2D55" d="M12 20.4S3.6 15.1 3.6 9.5a4.4 4.4 0 0 1 8.4-1.8 4.4 4.4 0 0 1 8.4 1.8c0 5.6-8.4 10.9-8.4 10.9"/></svg>`,
  notes: `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="4" y="3" width="16" height="18" rx="2.5" fill="#FFD60A"/>
    <path stroke="#8a6d00" stroke-width="1.4" stroke-linecap="round" d="M8 8h8M8 12h8M8 16h5"/></svg>`,
};

//: Brand marks, as the path each vendor publishes plus the colour they use.
//:
//: **Fetched once, at development time, and committed — never by the page.**
//: An image pulled from a vendor's CDN is a request that tells them which app
//: the user is running, which is the one thing this product promises does not
//: happen.  is what produced this; re-run it to
//: refresh, and the bytes land here rather than in a network call.
//:
//: Single-path monochrome marks from simple-icons (CC0), tinted with each
//: brand's own hex. The trademarks stay their owners' — naming a service you
//: connect to is what a mark is for. Anything not published there keeps a
//: monogram, which is honest: we would rather show a letter than a rough
//: approximation of somebody's logo.
const BRAND_MARKS = {
  airtable: ["M11.992 1.966c-.434 0-.87.086-1.28.257L1.779 5.917c-.503.208-.49.908.012 1.116l8.982 3.558a3.266 3.266 0 0 0 2.454 0l8.982-3.558c.503-.196.503-.908.012-1.116l-8.957-3.694a3.255 3.255 0 0 0-1.272-.257zM23.4 8.056a.589.589 0 0 0-.222.045l-10.012 3.877a.612.612 0 0 0-.38.564v8.896a.6.6 0 0 0 .821.552L23.62 18.1a.583.583 0 0 0 .38-.551V8.653a.6.6 0 0 0-.6-.596zM.676 8.095a.644.644 0 0 0-.48.19C.086 8.396 0 8.53 0 8.69v8.355c0 .442.515.737.908.54l6.27-3.006.307-.147 2.969-1.436c.466-.22.43-.908-.061-1.092L.883 8.138a.57.57 0 0 0-.207-.044z", "#18BFFF"],
  asana: ["M18.78 12.653c-2.882 0-5.22 2.336-5.22 5.22s2.338 5.22 5.22 5.22 5.22-2.34 5.22-5.22-2.336-5.22-5.22-5.22zm-13.56 0c-2.88 0-5.22 2.337-5.22 5.22s2.338 5.22 5.22 5.22 5.22-2.338 5.22-5.22-2.336-5.22-5.22-5.22zm12-6.525c0 2.883-2.337 5.22-5.22 5.22-2.882 0-5.22-2.337-5.22-5.22 0-2.88 2.338-5.22 5.22-5.22 2.883 0 5.22 2.34 5.22 5.22z", "#F06A6A"],
  atlassian: ["M7.12 11.084a.683.683 0 00-1.16.126L.075 22.974a.703.703 0 00.63 1.018h8.19a.678.678 0 00.63-.39c1.767-3.65.696-9.203-2.406-12.52zM11.434.386a15.515 15.515 0 00-.906 15.317l3.95 7.9a.703.703 0 00.628.388h8.19a.703.703 0 00.63-1.017L12.63.38a.664.664 0 00-1.196.006z", "#0052CC"],
  canva: ["M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zM6.962 7.68c.754 0 1.337.549 1.405 1.2.069.583-.171 1.097-.822 1.406-.343.171-.48.172-.549.069-.034-.069 0-.137.069-.206.617-.514.617-.926.548-1.508-.034-.378-.308-.618-.583-.618-1.2 0-2.914 2.674-2.674 4.629.103.754.549 1.646 1.509 1.646.308 0 .65-.103.96-.24.5-.264.799-.47 1.097-.8-.073-.885.704-2.046 1.851-2.046.515 0 .926.205.96.583.068.514-.377.582-.514.582s-.378-.034-.378-.17c-.034-.138.309-.07.275-.378-.035-.206-.24-.274-.446-.274-.72 0-1.131.994-1.029 1.611.035.275.172.549.447.549.205 0 .514-.31.617-.755.068-.308.343-.514.583-.514.102 0 .17.034.205.171v.138c-.034.137-.137.548-.102.651 0 .069.034.171.17.171.092 0 .436-.18.777-.459.117-.59.253-1.298.253-1.357.034-.24.137-.48.617-.48.103 0 .171.034.205.171v.138l-.136.617c.445-.583 1.097-.994 1.508-.994.172 0 .309.102.309.274 0 .103 0 .274-.069.446-.137.377-.309.96-.412 1.474 0 .137.035.274.207.274.171 0 .685-.206 1.096-.754l.007-.004c-.002-.068-.007-.134-.007-.202 0-.411.035-.754.104-.994.068-.274.411-.514.617-.514.103 0 .205.069.205.171 0 .035 0 .103-.034.137-.137.446-.24.857-.24 1.269 0 .24.034.582.102.788 0 .034.035.069.07.069.068 0 .548-.445.89-1.028-.308-.206-.48-.549-.48-.96 0-.72.446-1.097.858-1.097.343 0 .617.24.617.72 0 .308-.103.65-.274.96h.102a.77.77 0 0 0 .584-.24.293.293 0 0 1 .134-.117c.335-.425.83-.74 1.41-.74.48 0 .924.205.959.582.068.515-.378.618-.515.618l-.002-.002c-.138 0-.377-.035-.377-.172 0-.137.309-.068.274-.376-.034-.206-.24-.275-.446-.275-.686 0-1.13.891-1.028 1.611.034.275.171.583.445.583.206 0 .515-.308.652-.754.068-.274.343-.514.583-.514.103 0 .17.034.205.171 0 .069 0 .206-.137.652-.17.308-.171.48-.137.617.034.274.171.48.309.583.034.034.068.102.068.102 0 .069-.034.138-.137.138-.034 0-.068 0-.103-.035-.514-.205-.72-.548-.789-.891-.205.24-.445.377-.72.377-.445 0-.89-.411-.96-.926a1.609 1.609 0 0 1 .075-.649c-.203.13-.422.203-.623.203h-.17c-.447.652-.927 1.098-1.27 1.303a.896.896 0 0 1-.377.104c-.068 0-.171-.035-.205-.104-.095-.152-.156-.392-.193-.667-.481.527-1.145.805-1.453.805-.343 0-.548-.206-.582-.55v-.376c.102-.754.377-1.2.377-1.337a.074.074 0 0 0-.069-.07c-.24 0-1.028.824-1.166 1.373l-.103.445c-.068.309-.377.515-.582.515-.103 0-.172-.035-.206-.172v-.137l.046-.233c-.435.31-.87.508-1.075.508-.308 0-.48-.172-.514-.412-.206.274-.445.412-.754.412-.352 0-.696-.24-.862-.593-.244.275-.523.553-.852.764-.48.309-1.028.549-1.68.549-.582 0-1.097-.309-1.371-.583-.412-.377-.651-.96-.686-1.509-.205-1.68.823-3.84 2.4-4.8.378-.205.755-.343 1.132-.343zm9.77 3.291c-.104 0-.172.172-.172.343 0 .274.137.583.309.755a1.74 1.74 0 0 0 .102-.583c0-.343-.137-.515-.24-.515z", "#00C4CC"],
  clickup: ["M2 18.439l3.69-2.828c1.961 2.56 4.044 3.739 6.363 3.739 2.307 0 4.33-1.166 6.203-3.704L22 18.405C19.298 22.065 15.941 24 12.053 24 8.178 24 4.788 22.078 2 18.439zM12.04 6.15l-6.568 5.66-3.036-3.52L12.055 0l9.543 8.296-3.05 3.509z", "#7B68EE"],
  cloudflare: ["M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0 .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0 .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383.0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707.1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055.0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204.1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283.0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082.1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727", "#F38020"],
  datadog: ["M19.57 17.04l-1.997-1.316-1.665 2.782-1.937-.567-1.706 2.604.087.82 9.274-1.71-.538-5.794zm-8.649-2.498l1.488-.204c.241.108.409.15.697.223.45.117.97.23 1.741-.16.18-.088.553-.43.704-.625l6.096-1.106.622 7.527-10.444 1.882zm11.325-2.712l-.602.115L20.488 0 .789 2.285l2.427 19.693 2.306-.334c-.184-.263-.471-.581-.96-.989-.68-.564-.44-1.522-.039-2.127.53-1.022 3.26-2.322 3.106-3.956-.056-.594-.15-1.368-.702-1.898-.02.22.017.432.017.432s-.227-.289-.34-.683c-.112-.15-.2-.199-.319-.4-.085.233-.073.503-.073.503s-.186-.437-.216-.807c-.11.166-.137.48-.137.48s-.241-.69-.186-1.062c-.11-.323-.436-.965-.343-2.424.6.421 1.924.321 2.44-.439.171-.251.288-.939-.086-2.293-.24-.868-.835-2.16-1.066-2.651l-.028.02c.122.395.374 1.223.47 1.625.293 1.218.372 1.642.234 2.204-.116.488-.397.808-1.107 1.165-.71.358-1.653-.514-1.713-.562-.69-.55-1.224-1.447-1.284-1.883-.062-.477.275-.763.445-1.153-.243.07-.514.192-.514.192s.323-.334.722-.624c.165-.109.262-.178.436-.323a9.762 9.762 0 0 0-.456.003s.42-.227.855-.392c-.318-.014-.623-.003-.623-.003s.937-.419 1.678-.727c.509-.208 1.006-.147 1.286.257.367.53.752.817 1.569.996.501-.223.653-.337 1.284-.509.554-.61.99-.688.99-.688s-.216.198-.274.51c.314-.249.66-.455.66-.455s-.134.164-.259.426l.03.043c.366-.22.797-.394.797-.394s-.123.156-.268.358c.277-.002.838.012 1.056.037 1.285.028 1.552-1.374 2.045-1.55.618-.22.894-.353 1.947.68.903.888 1.609 2.477 1.259 2.833-.294.295-.874-.115-1.516-.916a3.466 3.466 0 0 1-.716-1.562 1.533 1.533 0 0 0-.497-.85s.23.51.23.96c0 .246.03 1.165.424 1.68-.039.076-.057.374-.1.43-.458-.554-1.443-.95-1.604-1.067.544.445 1.793 1.468 2.273 2.449.453.927.186 1.777.416 1.997.065.063.976 1.197 1.15 1.767.306.994.019 2.038-.381 2.685l-1.117.174c-.163-.045-.273-.068-.42-.153.08-.143.241-.5.243-.572l-.063-.111c-.348.492-.93.97-1.414 1.245-.633.359-1.363.304-1.838.156-1.348-.415-2.623-1.327-2.93-1.566 0 0-.01.191.048.234.34.383 1.119 1.077 1.872 1.56l-1.605.177.759 5.908c-.337.048-.39.071-.757.124-.325-1.147-.946-1.895-1.624-2.332-.599-.384-1.424-.47-2.214-.314l-.05.059a2.851 2.851 0 0 1 1.863.444c.654.413 1.181 1.481 1.375 2.124.248.822.42 1.7-.248 2.632-.476.662-1.864 1.028-2.986.237.3.481.705.876 1.25.95.809.11 1.577-.03 2.106-.574.452-.464.69-1.434.628-2.456l.714-.104.258 1.834 11.827-1.424zM15.05 6.848c-.034.075-.085.125-.007.37l.004.014.013.032.032.073c.14.287.295.558.552.696.067-.011.136-.019.207-.023.242-.01.395.028.492.08.009-.048.01-.119.005-.222-.018-.364.072-.982-.626-1.308-.264-.122-.634-.084-.757.068a.302.302 0 0 1 .058.013c.186.066.06.13.027.207m1.958 3.392c-.092-.05-.52-.03-.821.005-.574.068-1.193.267-1.328.372-.247.191-.135.523.047.66.511.382.96.638 1.432.575.29-.038.546-.497.728-.914.124-.288.124-.598-.058-.698m-5.077-2.942c.162-.154-.805-.355-1.556.156-.554.378-.571 1.187-.041 1.646.053.046.096.078.137.104a4.77 4.77 0 0 1 1.396-.412c.113-.125.243-.345.21-.745-.044-.542-.455-.456-.146-.749", "#632CA6"],
  figma: ["M15.852 8.981h-4.588V0h4.588c2.476 0 4.49 2.014 4.49 4.49s-2.014 4.491-4.49 4.491zM12.735 7.51h3.117c1.665 0 3.019-1.355 3.019-3.019s-1.355-3.019-3.019-3.019h-3.117V7.51zm0 1.471H8.148c-2.476 0-4.49-2.014-4.49-4.49S5.672 0 8.148 0h4.588v8.981zm-4.587-7.51c-1.665 0-3.019 1.355-3.019 3.019s1.354 3.02 3.019 3.02h3.117V1.471H8.148zm4.587 15.019H8.148c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h4.588v8.98zM8.148 8.981c-1.665 0-3.019 1.355-3.019 3.019s1.355 3.019 3.019 3.019h3.117V8.981H8.148zM8.172 24c-2.489 0-4.515-2.014-4.515-4.49s2.014-4.49 4.49-4.49h4.588v4.441c0 2.503-2.047 4.539-4.563 4.539zm-.024-7.51a3.023 3.023 0 0 0-3.019 3.019c0 1.665 1.365 3.019 3.044 3.019 1.705 0 3.093-1.376 3.093-3.068v-2.97H8.148zm7.704 0h-.098c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h.098c2.476 0 4.49 2.014 4.49 4.49s-2.014 4.49-4.49 4.49zm-.097-7.509c-1.665 0-3.019 1.355-3.019 3.019s1.355 3.019 3.019 3.019h.098c1.665 0 3.019-1.355 3.019-3.019s-1.355-3.019-3.019-3.019h-.098z", "#F24E1E"],
  github: ["M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", "#181717"],
  google_fit: ["M23.218 4.868c-1.235-2.194-3.927-3.356-6.378-2.843-1.11.243-2.173.774-2.979 1.583-.622.613-1.242 1.229-1.864 1.841-.915-.91-1.788-1.937-2.882-2.648a5.98 5.98 0 0 0-3.904-.845c-4.757.578-6.936 6.346-3.615 9.85 3.481 3.418 6.937 6.863 10.413 10.288 3.291-3.251 6.573-6.51 9.871-9.752 2.132-1.831 2.8-5.026 1.338-7.474zM6.162 11.223c-.692-.755-1.511-1.404-2.141-2.208-.821-1.218-.158-3.012 1.26-3.397.781-.256 1.683-.031 2.279.527.627.609 1.236 1.237 1.866 1.843l.005.006a414.706 414.706 0 0 0-3.269 3.229zm5.846 5.758a3300.079 3300.079 0 0 1-3.255-3.22c2.555-2.516 5.103-5.042 7.65-7.566.393-.394.93-.646 1.487-.673 2.086-.154 3.285 2.372 1.801 3.866-2.549 2.542-5.121 5.062-7.683 7.593z", "#4285F4"],
  intercom: ["M21 0H3C1.343 0 0 1.343 0 3v18c0 1.658 1.343 3 3 3h18c1.658 0 3-1.342 3-3V3c0-1.657-1.342-3-3-3zm-5.801 4.399c0-.44.36-.8.802-.8.44 0 .8.36.8.8v10.688c0 .442-.36.801-.8.801-.443 0-.802-.359-.802-.801V4.399zM11.2 3.994c0-.44.357-.799.8-.799s.8.359.8.799v11.602c0 .44-.357.8-.8.8s-.8-.36-.8-.8V3.994zm-4 .405c0-.44.359-.8.799-.8.443 0 .802.36.802.8v10.688c0 .442-.36.801-.802.801-.44 0-.799-.359-.799-.801V4.399zM3.199 6c0-.442.36-.8.802-.8.44 0 .799.358.799.8v7.195c0 .441-.359.8-.799.8-.443 0-.802-.36-.802-.8V6zM20.52 18.202c-.123.105-3.086 2.593-8.52 2.593-5.433 0-8.397-2.486-8.521-2.593-.335-.288-.375-.792-.086-1.128.285-.334.79-.375 1.125-.09.047.041 2.693 2.211 7.481 2.211 4.848 0 7.456-2.186 7.479-2.207.334-.289.839-.25 1.128.086.289.336.25.84-.086 1.128zm.281-5.007c0 .441-.36.8-.801.8-.441 0-.801-.36-.801-.8V6c0-.442.361-.8.801-.8.441 0 .801.357.801.8v7.195z", "#6AFDEF"],
  linear: ["M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z", "#5E6AD2"],
  notion: ["M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z", "#000000"],
  paypal: ["M7.016 19.198h-4.2a.562.562 0 0 1-.555-.65L5.093.584A.692.692 0 0 1 5.776 0h7.222c3.417 0 5.904 2.488 5.846 5.5-.006.25-.027.5-.066.747A6.794 6.794 0 0 1 12.071 12H8.743a.69.69 0 0 0-.682.583l-.325 2.056-.013.083-.692 4.39-.015.087zM19.79 6.142c-.01.087-.01.175-.023.261a7.76 7.76 0 0 1-7.695 6.598H9.007l-.283 1.795-.013.083-.692 4.39-.134.843-.014.088H6.86l-.497 3.15a.562.562 0 0 0 .555.65h3.612c.34 0 .63-.249.683-.585l.952-6.031a.692.692 0 0 1 .683-.584h2.126a6.793 6.793 0 0 0 6.707-5.752c.306-1.95-.466-3.744-1.89-4.906z", "#003087"],
  sentry: ["M13.91 2.505c-.873-1.448-2.972-1.448-3.844 0L6.904 7.92a15.478 15.478 0 0 1 8.53 12.811h-2.221A13.301 13.301 0 0 0 5.784 9.814l-2.926 5.06a7.65 7.65 0 0 1 4.435 5.848H2.194a.365.365 0 0 1-.298-.534l1.413-2.402a5.16 5.16 0 0 0-1.614-.913L.296 19.275a2.182 2.182 0 0 0 .812 2.999 2.24 2.24 0 0 0 1.086.288h6.983a9.322 9.322 0 0 0-3.845-8.318l1.11-1.922a11.47 11.47 0 0 1 4.95 10.24h5.915a17.242 17.242 0 0 0-7.885-15.28l2.244-3.845a.37.37 0 0 1 .504-.13c.255.14 9.75 16.708 9.928 16.9a.365.365 0 0 1-.327.543h-2.287c.029.612.029 1.223 0 1.831h2.297a2.206 2.206 0 0 0 1.922-3.31z", "#362D59"],
  slack: ["M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z", "#4A154B"],
  square: ["M4.01 0A4.01 4.01 0 000 4.01v15.98c0 2.21 1.8 4 4.01 4.01h15.98C22.2 24 24 22.2 24 19.99V4A4.01 4.01 0 0019.99 0H4zm1.62 4.36h12.74c.7 0 1.26.57 1.26 1.27v12.74c0 .7-.56 1.27-1.26 1.27H5.63c-.7 0-1.26-.57-1.26-1.27V5.63a1.27 1.27 0 011.26-1.27zm3.83 4.35a.73.73 0 00-.73.73v5.09c0 .4.32.72.72.72h5.1a.73.73 0 00.73-.72V9.44a.73.73 0 00-.73-.73h-5.1Z", "#3E4348"],
  stripe: ["M13.976 9.15c-2.172-.806-3.356-1.426-3.356-2.409 0-.831.683-1.305 1.901-1.305 2.227 0 4.515.858 6.09 1.631l.89-5.494C18.252.975 15.697 0 12.165 0 9.667 0 7.589.654 6.104 1.872 4.56 3.147 3.757 4.992 3.757 7.218c0 4.039 2.467 5.76 6.476 7.219 2.585.92 3.445 1.574 3.445 2.583 0 .98-.84 1.545-2.354 1.545-1.875 0-4.965-.921-6.99-2.109l-.9 5.555C5.175 22.99 8.385 24 11.714 24c2.641 0 4.843-.624 6.328-1.813 1.664-1.305 2.525-3.236 2.525-5.732 0-4.128-2.524-5.851-6.594-7.305h.003z", "#635BFF"],
  telegram: ["M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z", "#26A5E4"],
  todoist: ["M21 0H3C1.35 0 0 1.35 0 3v3.858s3.854 2.24 4.098 2.38c.31.18.694.177 1.004 0 .26-.147 8.02-4.608 8.136-4.675.279-.161.58-.107.748-.01.164.097.606.348.84.48.232.134.221.502.013.622l-9.712 5.59c-.346.2-.69.204-1.048.002C3.478 10.907.998 9.463 0 8.882v2.02l4.098 2.38c.31.18.694.177 1.004 0 .26-.147 8.02-4.609 8.136-4.676.279-.16.58-.106.748-.008.164.096.606.347.84.48.232.133.221.5.013.62-.208.121-9.288 5.346-9.712 5.59-.346.2-.69.205-1.048.002C3.478 14.951.998 13.506 0 12.926v2.02l4.098 2.38c.31.18.694.177 1.004 0 .26-.147 8.02-4.609 8.136-4.676.279-.16.58-.106.748-.009.164.097.606.348.84.48.232.133.221.502.013.622l-9.712 5.59c-.346.199-.69.204-1.048.001C3.478 18.994.998 17.55 0 16.97V21c0 1.65 1.35 3 3 3h18c1.65 0 3-1.35 3-3V3c0-1.65-1.35-3-3-3z", "#E44332"],
  vercel: ["M24 22.525H0l12-21.05 12 21.05z", "#000000"],
  webflow: ["m24 4.515-7.658 14.97H9.149l3.205-6.204h-.144C9.566 16.713 5.621 18.973 0 19.485v-6.118s3.596-.213 5.71-2.435H0V4.515h6.417v5.278l.144-.001 2.622-5.277h4.854v5.244h.144l2.72-5.244H24Z", "#146EF5"],
  zapier: ["M4.157 0A4.151 4.151 0 0 0 0 4.161v15.678A4.151 4.151 0 0 0 4.157 24h15.682A4.152 4.152 0 0 0 24 19.839V4.161A4.152 4.152 0 0 0 19.839 0H4.157Zm10.61 8.761h.03a.577.577 0 0 1 .23.038.585.585 0 0 1 .201.124.63.63 0 0 1 .162.431.612.612 0 0 1-.162.435.58.58 0 0 1-.201.128.58.58 0 0 1-.23.042.529.529 0 0 1-.235-.042.585.585 0 0 1-.332-.328.559.559 0 0 1-.038-.235.613.613 0 0 1 .17-.431.59.59 0 0 1 .405-.162Zm2.853 1.572c.03.004.061.004.095.004.325-.011.646.064.937.219.238.144.431.355.552.609.128.279.189.582.185.888v.193a2 2 0 0 1 0 .219h-2.498c.003.227.075.45.204.642a.78.78 0 0 0 .646.265.714.714 0 0 0 .484-.136.642.642 0 0 0 .23-.318l.915.257a1.398 1.398 0 0 1-.28.537c-.14.159-.321.284-.521.355a2.234 2.234 0 0 1-.836.136 1.923 1.923 0 0 1-1.001-.245 1.618 1.618 0 0 1-.665-.703 2.221 2.221 0 0 1-.227-1.036 1.95 1.95 0 0 1 .48-1.398 1.9 1.9 0 0 1 1.3-.488Zm-9.607.023c.162.004.325.026.48.079.207.065.4.174.563.314.26.302.393.692.366 1.088v2.276H8.53l-.109-.711h-.065c-.064.163-.155.31-.272.439a1.122 1.122 0 0 1-.374.264 1.023 1.023 0 0 1-.453.083 1.334 1.334 0 0 1-.866-.264.965.965 0 0 1-.329-.801.993.993 0 0 1 .076-.431 1.02 1.02 0 0 1 .242-.363 1.478 1.478 0 0 1 1.043-.303h.952v-.181a.696.696 0 0 0-.136-.454.553.553 0 0 0-.438-.154.695.695 0 0 0-.378.086.48.48 0 0 0-.193.254l-.99-.144a1.26 1.26 0 0 1 .257-.563c.14-.174.321-.302.533-.378.261-.091.54-.136.82-.129.053-.003.106-.007.163-.007Zm4.384.007c.174 0 .347.038.506.114.182.083.34.211.458.374.257.423.377.911.351 1.406a2.53 2.53 0 0 1-.355 1.448 1.148 1.148 0 0 1-1.009.517c-.204 0-.401-.045-.582-.136a1.052 1.052 0 0 1-.48-.457 1.298 1.298 0 0 1-.114-.234h-.045l.004 1.784h-1.059v-4.713h.904l.117.805h.057c.068-.208.177-.401.328-.56a1.129 1.129 0 0 1 .843-.344h.076v-.004Zm7.559.084h.903l.113.805h.053a1.37 1.37 0 0 1 .235-.484.813.813 0 0 1 .313-.242.82.82 0 0 1 .39-.076h.234v1.051h-.401a.662.662 0 0 0-.313.008.623.623 0 0 0-.272.155.663.663 0 0 0-.174.26.683.683 0 0 0-.027.314v1.875h-1.054v-3.666Zm-17.515.003h3.262v.896L3.73 13.104l.034.113h1.973l.042.9H2.4v-.9l1.931-1.754-.045-.117H2.441v-.896Zm11.815 0h1.055v3.659h-1.055V10.45Zm3.443.684.019.016a.69.69 0 0 0-.351.045.756.756 0 0 0-.287.204c-.11.155-.174.336-.189.522h1.545c-.034-.526-.257-.787-.74-.787h.003Zm-5.718.163c-.026 0-.057 0-.083.004a.78.78 0 0 0-.31.053.746.746 0 0 0-.257.189 1.016 1.016 0 0 0-.204.695v.064c-.015.257.057.507.204.711a.634.634 0 0 0 .253.196.638.638 0 0 0 .314.061.644.644 0 0 0 .578-.265c.14-.223.204-.48.189-.74a1.216 1.216 0 0 0-.181-.711.677.677 0 0 0-.503-.257Zm-4.509 1.266a.464.464 0 0 0-.268.102.373.373 0 0 0-.114.276c0 .053.008.106.027.155a.375.375 0 0 0 .087.132.576.576 0 0 0 .397.11v.004a.863.863 0 0 0 .563-.182.573.573 0 0 0 .211-.457v-.14h-.903Z", "#FF4F00"],
};

//: A neutral mark for anything without one — a custom API app, an MCP server,
//: or a connector added after this file was last touched.
const CONNECTOR_ICON_FALLBACK = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
  <rect x="3" y="3" width="18" height="18" rx="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <path stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M8 12h8M12 8v8"/></svg>`;

//: One line saying what the source actually gives the brain.
//:
//: No `group` any more — which group a source belongs to is now `on_device`,
//: answered by the connector itself. It was a map here, four connectors were
//: missing from it, and Slack, Telegram, Apple Health and Google Fit fell
//: through to a heading that said "Custom sources" about four things nobody
//: had customised. A fact the backend knows does not get a second copy here.
const CONNECTOR_META = {
  gmail:          { desc: "Your mail, read-only — threads, senders and what you agreed to." },
  gcal:           { desc: "Meetings, who is in them, and what your week looks like." },
  apple_mail:     { desc: "Mail from the Mail app on this Mac." },
  apple_calendar: { desc: "Events from the Calendar app on this Mac." },
  imessage:       { desc: "Messages on this Mac — who you talk to and about what." },
  notion:         { desc: "Pages and databases from your Notion workspace." },
  gdrive:         { desc: "Documents in your Drive, read-only." },
  files:          { desc: "A folder on this machine, indexed where it sits." },
  notes:          { desc: "Anything you type in yourself — the highest-trust source." },
  github:         { desc: "Issues, pull requests and what you are shipping." },
  linear:         { desc: "Issues, projects and cycles from your Linear workspace." },
  slack:          { desc: "Channels a bot has been invited to, and what was said in them." },
  telegram:       { desc: "Your Telegram conversations, read from this Mac." },
  apple_health:   { desc: "Readings from an Apple Health export — numbers, never memories." },
  google_fit:     { desc: "Activity and body measurements from your Google account." },
};

//: The two kinds of source, which is the difference a person can act on.
//:
//: Grouped by **where the data is**, not by topic. Topic was the old split
//: (mail / chat / docs / code) and it answered a question nobody was asking:
//: every row already says what it gives you. What no row said is whether
//: anything leaves this Mac, which is the entire product promise and the one
//: thing a person deciding whether to connect something actually wants.
const CONNECTOR_GROUPS = [
  { id: "device", title: "On this Mac",
    sub: "Read straight off this machine. No account is involved and nothing leaves it." },
  { id: "account", title: "Your accounts",
    sub: "You sign in with the service itself, and Chitragupta talks to it from this Mac. Nothing is routed through us." },
];

//: How a source is reached, as a word on the row.
//:
//: This names the mechanism, which `/CLAUDE.md` would normally call an
//: internal — the original note here said MCP is "an implementation detail
//: they never need". That was reversed deliberately: the person running this
//: asked to see which sources are ours and which are the vendor's own server,
//: because it decides who to chase when one misbehaves. The *sections* stay
//: jargon-free; only the tag names the route.
const CONNECTOR_KINDS = {
  builtin: { label: "Built-in", title: "Written into Chitragupta. We maintain it." },
  mcp:     { label: "MCP", title: "The service's own server, speaking the Model Context Protocol. The vendor maintains it and owns the schema." },
  custom:  { label: "Custom", title: "An API you pointed Chitragupta at yourself." },
};

//: The colour each mark already wears, lifted from its own artwork above, so a
//: tile's glow is that product's colour and not one house colour applied to
//: eleven different logos. Where the mark is monochrome (Notion, GitHub) the
//: value is the ink it is drawn in. Anything missing falls through to --star
//: via `.logo-tile`, which is the right answer for a custom app or an MCP
//: server: we do not know its colour, so we do not invent one.
const CONNECTOR_TINT = {
  gmail: "#EA4335", gcal: "#4285F4", gdrive: "#0F9D58",
  notion: "#ffffff", github: "#e6edf3", linear: "#5E6AD2",
  imessage: "#34C759", apple_mail: "#1F8DFB", apple_calendar: "#FF3B30",
  files: "#54A0FF", notes: "#FFD60A", apple_health: "#FF2D55",
};
function connectorIcon(name) {
  return CONNECTOR_ICONS[name] || CONNECTOR_ICON_FALLBACK;
}

//: A stable colour for a source we have no mark for.
//:
//: Derived from the id, the way `character.js` composes an agent's face from
//: its id — so a catalog of two dozen reads as two dozen objects rather than
//: one grey shape repeated, and a service added next year gets its own colour
//: without anybody picking one. Never fetched: an image pulled from a vendor's
//: CDN is a request that tells them which app the user is running, which is
//: the whole reason the marks above are drawn by hand.
function connectorHue(id) {
  let h = 0;
  for (let i = 0; i < String(id).length; i++) h = (h * 31 + String(id).charCodeAt(i)) % 360;
  return h;
}

//: The name a mark is looked up by.
//:
//: An MCP connector is `mcp:notion` and a custom app is `custom:<id>`, so a
//: straight lookup missed every one of them — Notion sat in the list wearing
//: the blank fallback while its mark was right there under `notion`. The
//: route is not part of the brand.
function markKey(name) {
  const at = String(name).indexOf(":");
  return at === -1 ? String(name) : String(name).slice(at + 1);
}

//: The mark for a source, in the order that gets it most right.
//:
//: 1. The vendor's own published path, where one exists. It is their shape,
//:    not our recollection of it, and it is the same at every size.
//: 2. The marks drawn by hand above, which cover the multi-colour ones
//:    nobody publishes as a single path — Gmail, Drive, Calendar, the Apple
//:    apps, a folder.
//: 3. A monogram. Deliberately a letter and not a rough approximation of
//:    somebody's logo, and deliberately not the neutral plus-in-a-box twenty
//:    times over, which is a list you cannot scan.
function connectorMark(id, name) {
  const brand = BRAND_MARKS[id];
  if (brand) {
    return `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path fill="currentColor" d="${esc(brand[0])}"/></svg>`;
  }
  if (CONNECTOR_ICONS[id]) return CONNECTOR_ICONS[id];
  const letter = String(name || id).trim().charAt(0).toUpperCase() || "?";
  return `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <text x="12" y="16.5" font-size="12" font-weight="700" text-anchor="middle"
      fill="currentColor" font-family="var(--sans), Helvetica, Arial"
      >${esc(letter)}</text></svg>`;
}

//: The colour a mark is drawn in: the brand's own, then ours, then derived.
//:
//: **Lifted off the floor first.** A single-path mark is filled with this, and
//: several brands are near-black by definition — GitHub is `#181717`, Notion
//: is `#000000`. On this ground that is a mark you cannot see at all, which is
//: why the hand-drawn GitHub above was already ink-coloured rather than black.
//: So anything below the floor is raised toward the page's own white, keeping
//: its hue: still recognisably the brand, and actually visible.
const MARK_MIN_LUMA = 0.32;

function markTint(id) {
  const raw = (BRAND_MARKS[id] && BRAND_MARKS[id][1]) || connectorTint(id);
  if (!raw) return `hsl(${connectorHue(id)} 62% 68%)`;
  const m = /^#?([0-9a-f]{6})$/i.exec(raw.trim());
  if (!m) return raw;
  const n = parseInt(m[1], 16);
  const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
  // Rec. 709, which is what "looks dark to a person" tracks, not the average.
  const luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  if (luma >= MARK_MIN_LUMA) return raw;
  const lift = (v) => Math.round((v + (1 - v) * (1 - luma / MARK_MIN_LUMA) * 0.82) * 255);
  return `rgb(${rgb.map(lift).join(",")})`;
}
function connectorTint(name) {
  return CONNECTOR_TINT[name] || "";
}
function connectorMeta(name) {
  return CONNECTOR_META[name] || { desc: "" };
}
//: Which section a row sits in, from the row itself rather than a lookup.
function connectorGroup(c) {
  return c.on_device ? "device" : "account";
}

// ── the connector list ─────────────────────────────────────────────────────
// Grouped, with each source's own mark, because a flat list of eleven names in
// one column is a list you read rather than a page you scan. The rows keep the
// same data-sync / data-setup / data-editapp / data-delapp / data-delmcp hooks
// the old list had, so every existing handler still finds its button.
let _cnRows = [];           // {name, label, group, ready, html} — for filtering
let _cnFilter = "all";

// The desktop app exposes `open_privacy_settings` on the pywebview bridge. It
// takes no arguments on purpose: the URL it opens is a constant in
// `connectors/permissions.py`, because `/api/open-browser` refuses custom
// schemes and widening that guard to reach a Settings pane is not a trade worth
// making. See chitragupta/connectors/permissions.py.
function canOpenPrivacySettings() {
  return !!window.pywebview?.api?.open_privacy_settings;
}

async function openPrivacySettings() {
  try {
    await window.pywebview.api.open_privacy_settings();
  } catch (_) {
    toast("Could not open System Settings. Open it yourself and go to " +
          "Privacy & Security → Full Disk Access, then turn on Chitragupta.");
  }
}

function _cnRowHtml(c, staleAfterMin) {
  const meta = connectorMeta(c.name);
  const ls = c.state?.last_sync ? new Date(c.state.last_sync) : null;
  const ageMin = ls ? (Date.now() - ls.getTime()) / 60000 : null;
  const stale = c.ready && ageMin !== null && ageMin > staleAfterMin;
  const last = ls ? ls.toLocaleDateString() : "";
  const state = !c.ready ? "off" : stale ? "stale" : "ok";
  // What the row says about itself: connected sources report their freshness,
  // unconnected ones say what they would give you if you connected them.
  // A connector with no listing tool is connected and useful — your agents can
  // ask it things — it just has nothing to pull in ahead of time. Saying
  // "not synced yet" about one would promise a sync that is never coming.
  const onDemand = c.mcp && c.ready && c.can_sync === false;
  // A `fix` means the server knows exactly what is wrong and that the user can
  // clear it. That reason outranks the catalogue blurb: "Reads your local
  // iMessages" is true and useless when macOS is the thing standing in the way.
  const blocked = !c.ready && !!c.fix;
  // A retired source says so. It keeps syncing and keeps everything it has
  // already put in the brain — what it no longer does is grow, because the
  // vendor's own server is the route now. Without this line the user reads a
  // connector that quietly stopped gaining features as one that is broken,
  // and has no idea there is somewhere to move to.
  const superseded = c.ready && c.superseded_by;
  const status = !c.ready ? (blocked ? c.reason : (meta.desc || c.reason || "Not connected"))
    : superseded ? `Connected · ${c.label}'s own server replaces this — disconnect to move across`
    : onDemand ? "Connected · answers your agents on demand"
    : !last ? "Connected · not synced yet"
    : stale ? `Connected · last synced ${last}` : `Connected · synced ${last}`;
  const badge = !c.ready ? ""
    : `<span class="cn-badge ${stale ? "is-stale" : ""}">${stale ? "Stale" : "Connected"}</span>`;
  // How it is reached, said on the row. `title` carries the difference for
  // anyone who wants it, so the tag itself can stay one word.
  const k = CONNECTOR_KINDS[c.kind] || CONNECTOR_KINDS.builtin;
  const kind = `<span class="cn-kind is-${esc(c.kind || "builtin")}" title="${esc(k.title)}">${esc(k.label)}</span>`;

  const sync = c.ready && !onDemand
    ? `<button class="tiny ghost" data-sync="${esc(c.name)}">Sync</button>` : "";
  // Opening a System Settings pane needs the native bridge, which exists only
  // in the desktop app — in a browser tab there is nothing behind the button,
  // and a control that cannot work is worse than no control. The sentence in
  // `status` already says where to go by hand, so the browser loses nothing.
  const fixBtn = blocked && c.fix === "full_disk_access" && canOpenPrivacySettings()
    ? `<button class="tiny" data-fda="${esc(c.name)}">Open Settings</button>` : "";
  const setup = c.custom
    ? `<button class="tiny ghost" data-editapp="${esc(c.name)}">Edit</button>`
    : (c.ready || fixBtn ? "" : `<button class="tiny" data-setup="${esc(c.name)}">Connect</button>`);
  // **A way out.** A token-backed connector had none: once it was ready the
  // row offered Sync and nothing else, so a source could be connected and
  // never unconnected — and a retirement the user cannot act on is a
  // retirement in name only. Telegram and Google already had their own; this
  // is the same control for everything that authenticates with a key.
  const disconnect = c.can_disconnect
    ? `<button class="tiny ghost" data-cnoff="${esc(c.name)}" data-cnlabel="${esc(c.label)}">Disconnect</button>`
    : "";
  const del = c.custom
    ? `<button class="tiny ghost cn-x" data-delapp="${esc(c.name)}" title="Remove" aria-label="Remove ${esc(c.label)}">${IC.close}</button>`
    : c.mcp ? `<button class="tiny ghost" data-cntools="${esc(c.name)}" data-cnlabel="${esc(c.label)}" title="What this connector can do">Permissions</button>
               <button class="tiny ghost cn-x" data-delmcp="${esc(c.name)}" title="Remove" aria-label="Remove ${esc(c.label)}">${IC.close}</button>` : "";

  return `<div class="cn-row" data-conn="${esc(c.name)}">
    <span class="cn-logo logo-tile" data-state="${state}" style="--brand:${
      markTint(markKey(c.name))
    };color:${markTint(markKey(c.name))}"><i class="lt-sheen"></i>${
      connectorMark(markKey(c.name), c.label)}</span>
    <span class="cn-text">
      <span class="cn-name">${esc(c.label)}${kind}${badge}</span>
      <span class="cn-sub">${esc(status)}</span>
    </span>
    <span class="cn-actions">${sync}${fixBtn}${setup}${disconnect}${del}</span>
  </div>`;
}

// Bound after every render, not once at load: the list is replaced wholesale
// on each filter keystroke, so a handler attached to the previous nodes is
// attached to nothing the user can click.
function bindConnectorRowActions() {
  document.querySelectorAll("[data-sync]").forEach((b) => b.onclick = () => syncConn(b.dataset.sync));
  document.querySelectorAll("[data-setup]").forEach((b) => b.onclick = () => connectorHelp(b.dataset.setup));
  document.querySelectorAll("[data-fda]").forEach((b) => b.onclick = () => openPrivacySettings());
  document.querySelectorAll("[data-editapp]").forEach((b) => b.onclick = () =>
    customAppForm(CONNECTORS.find((x) => x.name === b.dataset.editapp)?.config));
  document.querySelectorAll("[data-delapp]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.delapp.split(":")[1];
    if (!confirm("Remove this custom app? (synced records stay in the brain.)")) return;
    await api(`/api/custom-apps/${id}`, { method: "DELETE" });
    toast("custom app removed"); loadBrain();
  });
  document.querySelectorAll("[data-cnoff]").forEach((b) => b.onclick = async () => {
    // Says what it does and what it does not. The token goes; everything the
    // connector already put in the brain stays, the same promise removing an
    // MCP connector makes two handlers below.
    if (!confirm(`Disconnect ${b.dataset.cnlabel}? The saved key is forgotten. `
                 + "What it already synced stays in your brain.")) return;
    // The body goes as an OBJECT. `_encodeBody` only adds the JSON header for
    // an object — a `JSON.stringify` string passes through untouched, the
    // browser labels it text/plain, and FastAPI answers 422. That is exactly
    // the bug core.js records against /api/open-browser, and this button shipped
    // with it: the dialog appeared, OK did nothing, and nothing said why.
    try {
      await api(`/api/connectors/${encodeURIComponent(b.dataset.cnoff)}/secret`,
                { method: "POST", body: { value: "" } });
    } catch (e) {
      // And it is caught, which is the other half of that bug — the 422 was
      // invisible because the only caller swallowed it.
      toast(`Could not disconnect ${b.dataset.cnlabel}. ${String(e)}`);
      return;
    }
    toast(`${b.dataset.cnlabel} disconnected`); loadBrain();
  });
  document.querySelectorAll("[data-cntools]").forEach((b) => b.onclick = () =>
    connectorTools(b.dataset.cntools, b.dataset.cnlabel));
  document.querySelectorAll("[data-delmcp]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.delmcp.split(":")[1];
    // Say what removing does and does not do. Silently keeping the memories
    // would be a surprise; silently deleting them would be worse.
    if (!confirm("Remove this connector? (what it already synced stays in your brain.)")) return;
    await api(`/api/connectors/mcp/${encodeURIComponent(id)}`, { method: "DELETE" });
    toast("connector removed"); loadBrain();
  });
}

function renderConnectors(connectors, staleAfterMin) {
  const box = $("#connectors"); if (!box) return;
  // The catalog is part of this screen now, so it loads with it. Fired rather
  // than awaited — what you already have must render immediately, and a slow
  // catalog must never be what holds it up — and caught, because a section
  // that fails must not take the sources you already have down with it.
  Promise.resolve().then(loadConnectorCatalog).catch(() => {
    const avail = $("#cxList");
    if (avail) avail.textContent = "Could not load the connector list.";
  });
  _cnRows = connectors.map((c) => ({
    name: c.name, label: c.label, ready: Boolean(c.ready),
    group: connectorGroup(c),
    html: _cnRowHtml(c, staleAfterMin),
  }));
  renderConnectorFilters();
  applyConnectorFilter();
}

function renderConnectorFilters() {
  const box = $("#cnFilters"); if (!box) return;
  const total = _cnRows.length;
  const connected = _cnRows.filter((r) => r.ready).length;
  // Only groups that actually have a source are offered. A filter that can
  // only ever return nothing is a control that cannot work.
  const present = CONNECTOR_GROUPS.filter((g) => _cnRows.some((r) => r.group === g.id));
  const chips = [
    { id: "all", label: `All`, n: total },
    { id: "connected", label: `Connected`, n: connected },
    ...present.map((g) => ({ id: g.id, label: g.title, n: _cnRows.filter((r) => r.group === g.id).length })),
  ];
  box.innerHTML = chips.map((c) =>
    `<button type="button" role="tab" class="cn-chip${c.id === _cnFilter ? " is-on" : ""}" data-cnf="${c.id}"
      aria-selected="${c.id === _cnFilter}">${esc(c.label)} <span class="cn-chip-n">${c.n}</span></button>`).join("");
  box.querySelectorAll("[data-cnf]").forEach((b) => b.onclick = () => {
    _cnFilter = b.dataset.cnf;
    renderConnectorFilters();
    applyConnectorFilter();
  });
}

function applyConnectorFilter() {
  const box = $("#connectors"); if (!box) return;
  const q = (($("#cnSearch") || {}).value || "").trim().toLowerCase();
  const match = (r) => {
    if (q && !r.label.toLowerCase().includes(q) && !r.name.toLowerCase().includes(q)) return false;
    if (_cnFilter === "all") return true;
    if (_cnFilter === "connected") return r.ready;
    return r.group === _cnFilter;
  };
  const shown = _cnRows.filter(match);
  const sections = CONNECTOR_GROUPS.map((g) => {
    const rows = shown.filter((r) => r.group === g.id);
    if (!rows.length) return "";
    const conn = rows.filter((r) => r.ready).length;
    return `<section class="cn-group">
      <div class="cn-group-head">
        <div><h2 class="cn-group-title">${esc(g.title)}</h2><p class="cn-group-sub">${esc(g.sub)}</p></div>
        <span class="cn-group-count">${conn} of ${rows.length} connected</span>
      </div>
      <div class="cn-card">${rows.map((r) => r.html).join("")}</div>
    </section>`;
  }).join("");
  box.innerHTML = sections;
  const empty = $("#cnEmpty"); if (empty) empty.hidden = shown.length > 0;
  // The handlers are rebound here rather than delegated, because this markup is
  // replaced wholesale on every filter keystroke — a listener bound to a node
  // that a re-render has already detached is the bug these harnesses exist for.
  bindConnectorRowActions();
}

{
  const inp = $("#cnSearch");
  if (inp) inp.addEventListener("input", () => applyConnectorFilter());
}

const CONNECTOR_HELP = {
  gmail: `<p>Read-only access to your Gmail.</p><ol>
    <li>In <b>Google Cloud Console</b> → APIs & Services → Credentials, create an
        <b>OAuth client ID</b> of type <b>Desktop app</b>.</li>
    <li>Download the <code>client_secret.json</code>.</li>
    <li>Set <code>GOOGLE_CLIENT_SECRETS</code> to its path, or drop it at
        <code>~/Library/Chitragupta/google_client_secret.json</code>.</li>
    <li>Run a sync — a browser opens once to authorize (read-only).</li></ol>`,
  gdrive: `<p>Read-only access to your Google Drive (Docs, text, PDFs).</p>
    <p>Uses the <b>same Google OAuth Desktop client</b> as Gmail — set it up once
    (see the Gmail setup) and Drive works too.</p>`,
  gcal: `<p>Read-only access to your Google Calendar events.</p>
    <p>Uses the <b>same Google OAuth Desktop client</b> as Gmail — set it up once
    (see the Gmail setup). Then re-authorize once so Calendar scope is granted.</p>`,
  apple_mail: `<p>Reads mail straight off your Mac — <b>no Google sign-in</b>.
    Works if you have your account in the <b>Mail app</b>.</p><ol>
    <li>Add your email account in <b>Mail</b> (if not already).</li>
    <li><b>System Settings → Privacy & Security → Full Disk Access</b> → add your
        terminal / Chitragupta → enable.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>`,
  apple_calendar: `<p>Reads events off your Mac — <b>no sign-in</b>. Works with any
    calendar in the <b>Calendar app</b>.</p><ol>
    <li>Enable <b>Full Disk Access</b> for your terminal / Chitragupta.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>`,
  imessage: `<p>Reads your local iMessages (fully on-device, no cloud).</p><ol>
    <li>Open <b>System Settings → Privacy & Security → Full Disk Access</b>.</li>
    <li>Add your <b>Terminal</b> (or whatever runs Chitragupta) and enable it.</li>
    <li>Restart Chitragupta, then click sync.</li></ol>
    <p class="t">macOS only. Chitragupta only reads, never sends.</p>`,
  notion: `Read-only access to the Notion pages you share with an integration.`,
  linear: `Read-only access to your Linear issues (status, priority, team).`,
  github: `Read-only access to the GitHub issues & PRs you're involved in.`,
};
// ── Telegram ─────────────────────────────────────────────────────────────
//
// The one connector whose backend shipped complete and unreachable. Six
// endpoints and the whole Telethon flow existed; nothing in the frontend said
// the word "telegram" except a label constant. So `message_send` was an action
// the Inbox agent is taught and structurally could not take — the exact thing
// `/CLAUDE.md` forbids: never show a control that cannot work.
//
// Signing in takes up to four steps and the server owns which one you are on.
// `GET /api/telegram/status` is asked first and after anything that might have
// moved, rather than the modal keeping its own idea: a wizard that tracks its
// own position is a wizard that shows you step 2 after step 2 already
// succeeded in another window.

//: What each step of the sign-in asks for. The server decides which one is
//: current; this only says how each looks.
const TG_STEPS = {
  credentials: {
    title: "Connect Telegram",
    blurb: `<p>Telegram needs its own app credentials — Chitragupta cannot
      ship one, because an API ID identifies the app to Telegram and a shared
      one would be every user's traffic under a single name.</p>
      <ol>
        <li>Open <b>my.telegram.org</b> and sign in with your phone.</li>
        <li>Choose <b>API development tools</b> and fill the short form
            (any app name will do).</li>
        <li>Copy the <b>api_id</b> and <b>api_hash</b> it gives you.</li>
      </ol>
      <p style="margin:6px 0 12px"><a href="https://my.telegram.org/apps"
         target="_blank" rel="noopener">Open my.telegram.org →</a></p>`,
    fields: [["tgApiId", "api_id", "text", "1234567"],
             ["tgApiHash", "api_hash", "password", "your api_hash"]],
    button: "Save",
  },
  phone: {
    title: "Sign in to Telegram",
    blurb: `<p>Telegram will send a login code to this number, in the Telegram
      app itself.</p>`,
    fields: [["tgPhone", "Phone number, with country code", "tel", "+44…"]],
    button: "Send me a code",
  },
  code: {
    title: "Enter the code",
    blurb: `<p>Telegram has sent a code to your phone — check the Telegram app
      rather than your texts.</p>`,
    fields: [["tgCode", "Login code", "text", "12345"]],
    button: "Sign in",
  },
  password: {
    title: "Two-factor password",
    blurb: `<p>This account has a Telegram password (two-step verification).
      It never leaves your Mac.</p>`,
    fields: [["tgPassword", "Telegram password", "password", ""]],
    button: "Finish",
  },
};

function tgFields(step) {
  return step.fields.map(([id, label, type, placeholder]) => `
    <label class="t" style="display:block;margin:10px 0 4px">${esc(label)}</label>
    <input id="${id}" type="${type}" autocomplete="off" spellcheck="false"
           placeholder="${esc(placeholder)}"
           style="width:100%;padding:8px 10px;border:1px solid var(--line);
                  border-radius:8px;background:var(--bg);color:var(--text)" />`
  ).join("");
}

/** Open the Telegram modal at whichever step the server says we are on. */
async function telegramSetup(at = "") {
  let state = {};
  try {
    state = await api("/api/telegram/status");
  } catch {
    // The probe shells out to Telethon and can be slow or absent. A modal that
    // refuses to open teaches nobody anything; start at the beginning instead.
    state = { configured: false, authorized: false };
  }

  if (state.authorized) {
    openBrainModal("Telegram", `
      <p>Connected as <b>${esc(state.account || "your account")}</b>.</p>
      <p class="t">Your agents can read your chats, and send a message when you
         confirm one. The session lives on this Mac only.</p>
      <button id="tgOut" class="tiny ghost" style="margin-top:12px">Disconnect</button>`);
    $("#tgOut").onclick = async () => {
      $("#tgOut").disabled = true;
      try {
        const out = await api("/api/telegram/disconnect", { method: "POST" });
        toast(out.detail || "Telegram disconnected");
        $("#brainModal").hidden = true;
        loadBrain();
      } catch (e) { $("#tgOut").disabled = false; toast(String(e)); }
    };
    return;
  }

  // `at` lets a step move the flow on without re-asking; otherwise the server's
  // own answer decides, which is what makes a second window harmless.
  const which = at || (state.configured ? "phone" : "credentials");
  const step = TG_STEPS[which];
  openBrainModal(step.title, `${step.blurb}${tgFields(step)}
    <div style="display:flex;gap:8px;margin-top:12px;align-items:center">
      <button id="tgGo" class="tiny">${esc(step.button)}</button>
      ${which === "credentials" ? "" :
        `<button id="tgBack" class="tiny ghost">Start again</button>`}
      <span id="tgSay" class="t"></span>
    </div>
    <p class="t" style="margin-top:10px">Everything here is stored on this Mac
       only — never uploaded.</p>`);

  const first = $(`#${step.fields[0][0]}`);
  if (first) first.focus();
  const say = (words) => { const el = $("#tgSay"); if (el) el.textContent = words; };
  const back = $("#tgBack");
  if (back) back.onclick = () => telegramSetup("credentials");

  const go = $("#tgGo");
  go.onclick = async () => {
    const value = (id) => ($(`#${id}`)?.value || "").trim();
    go.disabled = true;
    say("…");
    try {
      let out;
      if (which === "credentials") {
        out = await api("/api/telegram/credentials", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_id: value("tgApiId"),
                                 api_hash: value("tgApiHash") }) });
        if (out.ok === false) throw new Error(out.error || "Telegram refused that");
        return telegramSetup("phone");
      }
      if (which === "phone") {
        out = await api("/api/telegram/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ phone: value("tgPhone") }) });
        if (!out.ok) throw new Error(out.error || "Telegram refused that");
        if (out.already) { toast("Already signed in"); return telegramSetup(); }
        return telegramSetup("code");
      }
      const path = which === "code" ? "code" : "password";
      out = await api(`/api/telegram/${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(which === "code"
          ? { code: value("tgCode") } : { password: value("tgPassword") }) });
      if (out.needs_password) {
        // Two-factor is on. Not a failure — the next step, and saying so is the
        // difference between a wizard and a dead end.
        return telegramSetup("password");
      }
      if (!out.ok) throw new Error(out.error || "Telegram refused that");
      toast(out.detail || "Telegram connected");
      $("#brainModal").hidden = true;
      loadBrain();
    } catch (e) {
      go.disabled = false;
      say(resultLine(e) || "That did not work.");
    }
  };
  for (const [id] of step.fields) {
    const box = $(`#${id}`);
    if (box) box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") go.click();
    });
  }
}

function connectorHelp(name) {
  // Telegram is a sign-in, not a pasted key, so it does not fit the
  // single-secret modal below.
  if (name === "telegram") return telegramSetup();
  const c = CONNECTORS.find((x) => x.name === name);
  const f = c?.secret_field;
  if (f) {
    // Connectors that authenticate with a single pasted token: show steps +
    // an in-app field (no .env editing, no restart needed).
    const steps = (f.steps || []).map((s) => `<li>${s}</li>`).join("");
    const link = f.help_url
      ? `<p style="margin:6px 0 12px"><a href="${f.help_url}" target="_blank" rel="noopener">Open ${c.label} to get your key →</a></p>` : "";
    openBrainModal(`Connect ${c.label}`,
      `<p>${CONNECTOR_HELP[name] || ""}</p>
       ${steps ? `<ol>${steps}</ol>` : ""}${link}
       <label class="t" style="display:block;margin-bottom:4px">${esc(f.label)}</label>
       <div style="display:flex;gap:8px">
         <input id="secretInput" type="password" autocomplete="off" spellcheck="false"
                placeholder="${esc(f.placeholder || "")}"
                style="flex:1;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font-family:monospace" />
         <button id="secretSave" class="tiny">Save</button>
       </div>
       <p class="t" style="margin-top:8px">Stored locally on your Mac only
         (<code>~/Library/Chitragupta/secrets.json</code>) — never uploaded.</p>`);
    const input = $("#secretInput");
    input.focus();
    $("#secretSave").onclick = async () => {
      const value = input.value.trim();
      if (!value) { toast("paste your key first"); return; }
      $("#secretSave").disabled = true; $("#secretSave").textContent = "Saving…";
      try {
        const r = await api(`/api/connectors/${name}/secret`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }) });
        if (r.ready) {
          toast(`${c.label} connected — syncing…`);
          $("#brainModal").hidden = true;
          await syncConn(name);
        } else {
          toast(r.reason || "saved, but not ready yet");
        }
        loadBrain();
      } catch (e) { toast(String(e)); }
      finally { $("#secretSave").disabled = false; $("#secretSave").textContent = "Save"; }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#secretSave").click(); });
    return;
  }
  openBrainModal(`Set up ${name}`, (CONNECTOR_HELP[name] || "<p>No setup needed.</p>")
    + `<p class="t" style="margin-top:10px">Add the value to your <code>.env</code> and restart Chitragupta.</p>`);
}

// ── custom API app: connect any REST app, no code ──────────────────────────
function customAppForm(app) {
  app = app || {};
  const row = (label, id, val, ph) =>
    `<label class="t" style="display:block;margin:8px 0 3px">${label}</label>
     <input id="${id}" value="${esc(val || "")}" placeholder="${esc(ph || "")}" spellcheck="false"
       style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;
  const at = app.auth_type || "none";
  const opt = (v, t) => `<option value="${v}"${at === v ? " selected" : ""}>${t}</option>`;
  openBrainModal(app.id ? `Edit ${app.name}` : "Connect a custom app",
    `<p class="t">Point Chitragupta at any REST API that returns JSON. It fetches the
       endpoint and adds each record to your brain. Stays on your Mac.</p>
     ${row("App name", "ca_name", app.name, "My CRM")}
     ${row("Base URL", "ca_base", app.base_url, "https://api.myapp.com/v1")}
     ${row("Endpoint", "ca_ep", app.endpoint, "/contacts")}
     <label class="t" style="display:block;margin:8px 0 3px">Auth</label>
     <select id="ca_auth" style="width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)">
       ${opt("none", "None")}${opt("bearer", "Bearer token")}${opt("header", "Custom header")}${opt("query", "Query parameter")}</select>
     ${row("Header / param name (for custom header or query)", "ca_authname", app.auth_name, "X-API-Key")}
     ${row("Token (leave blank to keep current)", "ca_token", "", "•••••••• stored locally, chmod 600")}
     <hr style="border:none;border-top:1px solid var(--line);margin:12px 0">
     <p class="t">Map the JSON (dot-paths, e.g. <code>data.results</code>):</p>
     ${row("Items path — where the list lives", "ca_items", app.items_path, "data.results")}
     ${row("Title field", "ca_title", app.title_field, "name")}
     ${row("Body field", "ca_body", app.body_field, "notes")}
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="ca_save" class="tiny">${app.id ? "Save changes" : "Save & sync"}</button></div>`);
  $("#ca_name").focus();
  $("#ca_save").onclick = async () => {
    const payload = {
      id: app.id || null,
      name: $("#ca_name").value.trim() || "Custom app",
      base_url: $("#ca_base").value.trim(),
      endpoint: $("#ca_ep").value.trim(),
      auth_type: $("#ca_auth").value,
      auth_name: $("#ca_authname").value.trim(),
      items_path: $("#ca_items").value.trim(),
      title_field: $("#ca_title").value.trim(),
      body_field: $("#ca_body").value.trim(),
    };
    const tok = $("#ca_token").value.trim();
    if (tok) payload.token = tok;
    if (!payload.base_url) { toast("base URL is required"); return; }
    $("#ca_save").disabled = true; $("#ca_save").textContent = "Saving…";
    try {
      const r = await api("/api/custom-apps", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) });
      $("#brainModal").hidden = true;
      toast("custom app saved — syncing…");
      await syncConn(r.name);
      loadBrain();
    } catch (e) { toast(String(e)); $("#ca_save").disabled = false; $("#ca_save").textContent = "Save"; }
  };
}
$("#addCustomApp").onclick = () => customAppForm();

// ── the connector catalog ────────────────────────────────────────────────
// Everything here is a "connector" to the user. Several are backed by MCP
// servers, which is an implementation detail they never need — the same way
// signing in to Claude never mentions a vendor CLI.

// The catalog lives ON the Connectors page now, not in a modal. This stays as
// the way back from the screens that drill into it — a permissions sheet, a
// setup form — and it closes whatever is open and returns you to the list,
// which is what "Back" meant when the list was a modal too.
function connectorBrowser() {
  const modal = $("#brainModal");
  if (modal) modal.hidden = true;
  loadConnectorCatalog();
  const box = $("#cnAvailable");
  if (box && box.scrollIntoView) box.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadConnectorCatalog() {
  const box = $("#cxList");
  if (!box) return;
  let data;
  try {
    data = await api("/api/connectors/catalog");
  } catch (e) {
    box.textContent = "Could not load the connector list. " + String(e);
    return;
  }
  // **A shape guard, not a nicety.** This list used to live in a modal, where
  // a throw took down something the user had deliberately opened. It is part
  // of the Connectors screen now, so the same throw would take down the
  // sources they already have — the half of the screen that must always
  // render. A reply missing `available` is treated as an empty catalog and
  // said out loud, never as a reason for the page to stop.
  data = data || {};
  if (!Array.isArray(data.available)) data.available = [];
  if (!Array.isArray(data.blocked)) data.blocked = [];

  // Same mark, same tile, same size as the sources above it — one screen, one
  // kind of row. A catalog entry carries no state dot: there is nothing
  // connected to report yet, and a dot that always means "off" is noise.
  const card = (c) => `
    <div class="cx-row" data-cx="${esc(c.id)}">
      <span class="cn-logo logo-tile" style="--brand:${markTint(c.id)
        };color:${markTint(c.id)}"><i class="lt-sheen"></i>${
        connectorMark(c.id, c.name)}</span>
      <span class="cx-text">
        <span class="conn-name">${esc(c.name)}</span>
        <span class="conn-sub">${c.added ? "already added"
          : esc(c.notes || (c.first_party ? "Official connector" : "Community connector"))}</span>
      </span>
      <button class="tiny${c.added ? " ghost" : ""}" data-cxadd="${esc(c.id)}"
        ${c.added ? "disabled" : ""}>${c.added ? "added" : "Add"}</button>
    </div>`;

  // The catalog is what we have vetted, and will never be all of it. Offering
  // the escape hatch here rather than hiding it in settings is the difference
  // between "these nine" and "anything you have".
  const own = `
    <div class="cx-row">
      <span>
        <span class="conn-name">Something else</span>
        <span class="conn-sub">Point Chitragupta at a server you already have.</span>
      </span>
      <button class="tiny ghost" id="cxOwn">Add your own</button>
    </div>`;

  // Grouped, because two dozen connectors in one list is a wall and the same
  // two dozen on six shelves is a decision. The order comes from the server —
  // the catalog knows what belongs where, and a second list here would
  // eventually disagree with it.
  const order = data.categories && data.categories.length
    ? data.categories
    : [...new Set(data.available.map((c) => c.category).filter(Boolean))];
  const shelved = order
    .map((cat) => [cat, data.available.filter((c) => c.category === cat)])
    .filter(([, items]) => items.length);
  // Anything the server grouped under a name we were not given still has to
  // appear. A connector that exists and is invisible is worse than an ugly
  // heading.
  const placed = new Set(shelved.flatMap(([, items]) => items.map((c) => c.id)));
  const rest = data.available.filter((c) => !placed.has(c.id));
  if (rest.length) shelved.push(["Other", rest]);

  const shelf = ([cat, items]) =>
    `<div class="cx-head">${esc(cat)}</div>${items.map(card).join("")}`;

  // **Sources nobody can offer are no longer listed here.** They used to be —
  // the argument was that a grid silently lacking LinkedIn teaches the user
  // this app is missing a feature, when the truth is that no app can offer it.
  // That argument held while this was a modal you opened to go shopping. On
  // the page it is three permanently dead rows at the bottom of a live list,
  // and the longest explanation on the screen belongs to the thing you cannot
  // have. The refusal is not lost: `add_from_catalog` still answers with
  // `BLOCKED`'s own sentence if one is ever asked for by id, which is where it
  // is actually useful — at the moment somebody tries.
  box.innerHTML = `${shelved.map(shelf).join("")}${own}`;

  box.querySelectorAll("[data-cxadd]").forEach((b) => {
    if (!b.disabled) b.onclick = () => connectorPermissions(b.dataset.cxadd);
  });
  $("#cxOwn").onclick = customServerForm;
}

// Consent to something nobody has been shown is not consent, so what a
// connector can do is read from the server and displayed before it is added.
//
// Three shapes, because the sources genuinely differ:
//   · the vendor signs you in  → a Connect button, then their own page
//   · the vendor wants a key   → a masked field
//   · a local server           → whatever it needs positionally, e.g. a folder
// A connector that needs nothing is probed up front and its tools listed.

function fieldRow(f, prefix) {
  const masked = f.kind === "secret";
  const hint = f.kind === "path" ? ' placeholder="~/Documents"' : "";
  return `
    <label class="t" style="display:block;margin:10px 0 3px">${esc(f.label)}</label>
    <div class="t" style="opacity:.7;margin-bottom:4px">${esc(f.help)}</div>
    <input id="${prefix}_${esc(f.name)}" spellcheck="false"${hint}
      type="${masked ? "password" : "text"}"
      style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;
}

function toolList(items, empty) {
  return items.length
    ? `<ul style="margin:4px 0 0 16px;padding:0">${
        items.map((t) => `<li><code>${esc(t)}</code></li>`).join("")}</ul>`
    : `<div class="t" style="opacity:.7;margin-top:4px">${esc(empty)}</div>`;
}

async function connectorPermissions(entryId) {
  openBrainModal("Add a connector",
    `<div id="cxPerm" class="t">Checking what this connector can do…</div>`);
  let info;
  try {
    info = await api(`/api/connectors/catalog/${encodeURIComponent(entryId)}/permissions`);
  } catch (e) {
    $("#cxPerm").textContent = "Could not check this connector. " + String(e);
    return;
  }

  if (!info.available) {
    $("#cxPerm").innerHTML =
      `<p class="t">${esc(info.reason || "This connector cannot be added.")}</p>
       <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
         <button id="cxBack" class="tiny ghost">Back</button></div>`;
    $("#cxBack").onclick = connectorBrowser;
    return;
  }

  const env = (info.needs_env || []).map((f) => fieldRow(f, "cxenv")).join("");
  const args = (info.needs_args || []).map((f) => fieldRow(f, "cxarg")).join("");

  // Nothing is known about a server behind someone else's sign-in until the
  // user has signed in. Promising a tool list we do not have would be a guess;
  // saying what happens next is not.
  const preview = info.needs_auth
    ? `<p class="t">You'll be sent to <b>${esc(info.name)}</b> to sign in.
         Chitragupta never sees your password, and the permissions you grant are
         shown on their page.</p>
       <p class="t" style="margin-top:8px;opacity:.8">Once connected, anything
         that <b>changes</b> something in ${esc(info.name)} always asks you first.</p>`
    : (info.reads.length || info.writes.length)
      ? `<p class="t"><b>${esc(info.name)}</b> would be able to:</p>
         <div style="margin-top:8px"><b class="t">Read</b>${toolList(info.reads, "nothing")}</div>
         <div style="margin-top:8px"><b class="t">Change</b>${
           toolList(info.writes, "nothing — this connector is read-only")}</div>
         ${info.writes.length ? `<p class="t" style="margin-top:8px;opacity:.8">
           Anything that changes something always asks you first.</p>` : ""}
         ${info.can_sync ? "" : `<p class="t" style="margin-top:8px">
           This one answers questions but cannot list its records, so it is
           searched on demand rather than synced.</p>`}`
      : `<p class="t">${esc(info.notes || "")}</p>
         <p class="t" style="margin-top:8px;opacity:.8">You'll see exactly what
           it can read and change as soon as it's connected.</p>`;

  $("#cxPerm").innerHTML =
    preview +
    ((env || args) ? `<hr style="border:none;border-top:1px solid var(--line);margin:12px 0">${args}${env}` : "") +
    `<div id="cxErr" class="t" style="color:var(--bad);margin-top:8px" hidden></div>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="cxBack" class="tiny ghost">Back</button>
       <button id="cxGo" class="tiny">${info.needs_auth ? "Connect" : "Add connector"}</button></div>`;

  $("#cxBack").onclick = connectorBrowser;
  $("#cxGo").onclick = async () => {
    const body = { env: {}, args: {} };
    (info.needs_env || []).forEach((f) => {
      const v = $(`#cxenv_${f.name}`);
      if (v && v.value.trim()) body.env[f.name] = v.value.trim();
    });
    (info.needs_args || []).forEach((f) => {
      const v = $(`#cxarg_${f.name}`);
      if (v && v.value.trim()) body.args[f.name] = v.value.trim();
    });
    const go = $("#cxGo"), err = $("#cxErr");
    go.disabled = true; go.textContent = "Checking…"; err.hidden = true;
    try {
      const r = await api(`/api/connectors/catalog/${encodeURIComponent(entryId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      if (!r.ok) {
        // Say why here, where they are looking — a failed add that closes the
        // dialog and shows nothing is the shape of "the app is broken".
        err.textContent = r.error; err.hidden = false;
        go.disabled = false; go.textContent = info.needs_auth ? "Connect" : "Add connector";
        return;
      }
      if (r.signing_in) { awaitSignIn(r.server_id, r.label); return; }
      $("#brainModal").hidden = true;
      toast(`${r.label} connected — syncing…`);
      await syncConn(r.name);
      loadBrain();
    } catch (e) {
      err.textContent = String(e); err.hidden = false;
      go.disabled = false; go.textContent = info.needs_auth ? "Connect" : "Add connector";
    }
  };
}

// ── waiting on the vendor's own sign-in ──────────────────────────────────
// The browser is somewhere else now, so this has to survive the user tabbing
// away and coming back — the status lives on the server, not in this closure.
// And anything they start, they can stop: Cancel really ends the flow.

async function awaitSignIn(serverId, label) {
  openBrainModal(`Connect ${label}`,
    `<p class="t">A browser window is opening. Sign in to <b>${esc(label)}</b>
       and approve the permissions you want to give it.</p>
     <p class="t" id="cxAuthState" style="margin-top:10px;opacity:.75">Waiting for you to finish…</p>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="cxAuthCancel" class="tiny ghost">Cancel</button></div>`);

  let stopped = false;
  $("#cxAuthCancel").onclick = async () => {
    stopped = true;
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/auth`,
              { method: "DELETE" }).catch(() => {});
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}`,
              { method: "DELETE" }).catch(() => {});
    $("#brainModal").hidden = true;
    toast(`${label} was not connected`);
    loadBrain();
  };

  for (let i = 0; i < 150 && !stopped; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    if (stopped) return;
    let s;
    try {
      s = await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/auth`);
    } catch { continue; }
    if (s.status === "connected") {
      $("#brainModal").hidden = true;
      toast(`${label} connected`);
      loadBrain();
      return;
    }
    if (s.status === "failed" || s.status === "cancelled") {
      const state = $("#cxAuthState");
      if (state) { state.textContent = s.reason || "That didn't complete."; state.style.color = "var(--bad)"; }
      return;
    }
  }
  const state = $("#cxAuthState");
  if (state && !stopped) state.textContent = "Still waiting — you can close this and try again.";
}

// ── a server we do not list ──────────────────────────────────────────────
// The catalog covers what we have vetted, which will never be all of it.

function customServerForm() {
  const input = (id, label, help, ph) => `
    <label class="t" style="display:block;margin:10px 0 3px">${label}</label>
    ${help ? `<div class="t" style="opacity:.7;margin-bottom:4px">${help}</div>` : ""}
    <input id="${id}" spellcheck="false" placeholder="${ph || ""}"
      style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)" />`;

  openBrainModal("Add your own connector",
    `<p class="t">Point Chitragupta at a server you already have. It is started
       and checked before it is saved, so a broken one is never added.</p>
     ${input("csName", "Name", "What you want to call it here.", "My tracker")}
     <label class="t" style="display:block;margin:10px 0 3px">Kind</label>
     <select id="csKind" style="width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)">
       <option value="http">A web address (the service runs it)</option>
       <option value="stdio">A program on this Mac</option>
     </select>
     <div id="csHttp">${input("csUrl", "Address", "", "https://mcp.example.com/mcp")}</div>
     <div id="csStdio" hidden>
       ${input("csCmd", "Command", "The program to run.", "npx")}
       ${input("csArgs", "Arguments", "Separated by spaces.", "-y some-mcp-server@1.0.0")}
     </div>
     <div id="csErr" class="t" style="color:var(--bad);margin-top:8px" hidden></div>
     <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
       <button id="csBack" class="tiny ghost">Back</button>
       <button id="csGo" class="tiny">Add connector</button></div>`);

  const sync = () => {
    const http = $("#csKind").value === "http";
    $("#csHttp").hidden = !http; $("#csStdio").hidden = http;
  };
  $("#csKind").onchange = sync; sync();
  $("#csBack").onclick = connectorBrowser;
  $("#csGo").onclick = async () => {
    const name = ($("#csName").value || "").trim();
    const kind = $("#csKind").value;
    const err = $("#csErr"), go = $("#csGo");
    if (!name) { err.textContent = "Give it a name."; err.hidden = false; return; }
    const body = {
      id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
      name, transport: kind,
      url: kind === "http" ? ($("#csUrl").value || "").trim() : "",
      command: kind === "stdio" ? ($("#csCmd").value || "").trim() : "",
      args: kind === "stdio"
        ? ($("#csArgs").value || "").trim().split(/\s+/).filter(Boolean) : [],
    };
    go.disabled = true; go.textContent = "Checking…"; err.hidden = true;
    try {
      const r = await api("/api/connectors/mcp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      if (!r.ok) {
        err.textContent = r.error; err.hidden = false;
        go.disabled = false; go.textContent = "Add connector"; return;
      }
      if (r.signing_in) { awaitSignIn(r.server_id, r.label); return; }
      $("#brainModal").hidden = true;
      toast(`${r.label} connected`);
      loadBrain();
    } catch (e) {
      err.textContent = String(e); err.hidden = false;
      go.disabled = false; go.textContent = "Add connector";
    }
  };
}

// ── what a connector is allowed to use ───────────────────────────────────
// Least privilege the user cannot set is a claim, not a control. This is the
// half that was missing: the tools were shown before adding and could never
// be changed afterwards.

async function connectorTools(name, label) {
  const serverId = name.split(":")[1];
  openBrainModal(`What ${label} can do`,
    `<div id="cxTools" class="t">Asking ${esc(label)}…</div>`);
  let info;
  try {
    info = await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}/tools`);
  } catch (e) {
    $("#cxTools").textContent = "Could not reach this connector. " + String(e);
    return;
  }
  if (!info.ok) { $("#cxTools").textContent = info.error || "Could not reach it."; return; }

  const allowed = new Set(info.allowed_tools || []);
  const everything = allowed.size === 0;
  const man = info.manifest || null;

  const row = (t) => `
    <label class="cx-tool">
      <input type="checkbox" data-tool="${esc(t.tool)}"
        ${everything || allowed.has(t.tool) ? "checked" : ""} />
      <span class="cx-tool-text">
        <code>${esc(t.tool)}</code>
        ${t.summary ? `<span class="conn-sub">${esc(t.summary)}</span>` : ""}
      </span>
    </label>`;

  // **Three groups, not one list.** A flat forty-five is an inventory, and
  // nobody consents to an inventory. What a person is deciding is what this
  // can reach and what it can change — so that is the shape, with the verbs
  // that cannot be taken back in a tier of their own rather than as a louder
  // shade of "changes".
  const group = (title, note, caps) => !caps.length ? "" : `
    <div class="cx-cap">
      <div class="cx-cap-head">${esc(title)}
        <span class="cx-cap-n">${caps.length}</span></div>
      ${note ? `<p class="cx-cap-note">${esc(note)}</p>` : ""}
      ${caps.map(row).join("")}
    </div>`;

  const chip = (on, text) => on
    ? `<span class="cx-fact">${esc(text)}</span>` : "";
  const v = (man && man.verification) || {};
  const b = (man && man.bounds) || {};

  $("#cxTools").innerHTML = !man
    // A server too old or too odd to describe still gets its switches. The
    // manifest is how this is *read*; the list is how it is *changed*, and
    // losing the second because the first is missing would be a worse screen
    // than the one this replaced.
    ? `<div style="margin-top:10px">${info.tools.map((t) =>
         row({ tool: t.name, summary: t.description })).join("")}</div>
       <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
         <button id="ctCancel" class="tiny ghost">Cancel</button>
         <button id="ctSave" class="tiny">Save</button></div>`
    : `<p class="cx-headline">${esc(man.headline)}</p>
       <div class="cx-facts">
         ${chip(man.auth !== "none", man.auth === "oauth"
             ? "You signed in with them" : "Uses a key you provided")}
         ${chip(man.remote, "Talks to their own server")}
         ${chip(!man.remote, "Runs on this Mac")}
         ${chip(v.first_party, "Published by the vendor")}
         ${chip(v.known && !v.first_party, "Community server")}
         ${chip(!v.known, "You added this one yourself")}
         ${chip(v.pinned, "Version pinned")}
         ${chip(man.restricted, "You have switched some tools off")}
       </div>
       ${group("Can read", "Answers questions. Your agents use these without asking.",
               man.reads.filter((c) => !c.furniture))}
       ${group("Can change", "Every one of these puts a card in front of you first.",
               man.changes)}
       ${group("Cannot be undone",
               "These always ask, every single time — no standing approval covers them.",
               man.needs_care)}
       ${group("Its own settings",
               "Lists about how this connector is configured, not about you.",
               man.reads.filter((c) => c.furniture))}
       <p class="cx-cap-note" style="margin-top:14px">Whatever you allow here,
         this app reads at most ${esc(b.records_per_sync || 200)} records a sync
         and gives any single call ${esc(b.seconds_per_call || 45)} seconds.
         Those are our limits, not ${esc(label)}'s.</p>
       <p class="cx-cap-note">Untick anything you would rather ${esc(label)}
         could not touch. Unticked tools are never offered to your agents.</p>
       <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
         <button id="ctCancel" class="tiny ghost">Cancel</button>
         <button id="ctSave" class="tiny">Save</button></div>`;

  $("#ctCancel").onclick = () => $("#brainModal").hidden = true;
  $("#ctSave").onclick = async () => {
    const boxes = [...document.querySelectorAll("#cxTools [data-tool]")];
    const on = boxes.filter((b) => b.checked).map((b) => b.dataset.tool);
    // All of them checked means "no restriction", which is stored as an empty
    // list — otherwise a tool added by a server update would arrive disabled.
    const body = { allowed_tools: on.length === boxes.length ? [] : on };
    await api(`/api/connectors/mcp/${encodeURIComponent(serverId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    $("#brainModal").hidden = true;
    toast(`${label} updated`);
    loadBrain();
  };
}

// The button is gone — the list it opened is on the page. Guarded rather than
// deleted outright because `app.js` is evaluated whole by the test harnesses,
// and a null here throws before anything under test is reached.
if ($("#addConnector")) $("#addConnector").onclick = () => connectorBrowser();

// ── waiting for approval ─────────────────────────────────────────────────
// An action an unattended agent wanted to take, held until the user decides.
// The queue, the notification and the endpoints existed before this; what did
// not was anywhere to look, which made a desktop notification the only trace a
// request ever happened. A queue nobody can see is not an approval system.
