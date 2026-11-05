/*
 * End-to-end test of the MCF discovery (v5.3.0, livrable B section 7) in a real Chromium (Playwright):
 *   cashier login -> page Poste MCF -> station of a point de vente WITHOUT MCF -> "Connecter le MCF"
 *   (serial port = MCF simulator) -> "Proposer ce MCF" (C1h, 2Bh, C2h read, no fiscal effect) -> MCF
 *   'Detecte' in ERPNext, not active -> validation by another person (bench) -> MCF Actif, station linked
 *   -> "Tester la connexion" recorded.
 *
 *   node e2e_discovery.js <site_url>
 * Requires: bench serve on <site_url>, `bench execute dgi_compliance.tests.e2e_station.prepare_discovery`,
 * PYTHON (bench env) and BENCH env vars. TEST SITES ONLY.
 */
"use strict";
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const CASHIER = "caisse.e2e@example.com", PASSWORD = "Caisse#E2E-2026!";
const NID = "DSC0000001";

const py = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "sim_bridge.py")], { stdio: ["pipe", "pipe", "inherit"] });
const rl = readline.createInterface({ input: py.stdout });
const waiting = [];
rl.on("line", (l) => waiting.shift()(JSON.parse(l)));
const ask = (req) => new Promise((r) => { waiting.push(r); py.stdin.write(JSON.stringify(req) + "\n"); });

function bench(method, kwargs) {
	const args = ["--site", process.env.SITE_NAME || "test.local", "execute", method];
	if (kwargs) args.push("--kwargs", JSON.stringify(kwargs));
	const out = execFileSync("su", ["-", "frappe", "-c", `[ -f ~/env.sh ] && . ~/env.sh; cd ${BENCH} && bench ${args.map((a) => `'${a}'`).join(" ")} 2>/dev/null`]).toString();
	return out.trim().split("\n").pop();
}
const state = () => JSON.parse(bench("dgi_compliance.tests.e2e_station.discovery_state").replace(/^STATE=/, ""));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };

const FAKE_SERIAL = () => {
	class FakePort {
		getInfo() { return { usbVendorId: 0x1a86, usbProductId: 0x7523 }; }
		async open() {
			const self = this;
			this.readable = new ReadableStream({ start(c) { self.ctrl = c; } });
			this.writable = new WritableStream({
				async write(chunk) {
					const hex = Array.from(chunk, (b) => b.toString(16).padStart(2, "0")).join("");
					const out = await window.__mcfWrite(hex);
					if (out) self.ctrl.enqueue(Uint8Array.from(out.match(/../g).map((h) => parseInt(h, 16))));
				},
			});
		}
		async close() { try { this.ctrl.close(); } catch (e) { /* closed */ } }
	}
	const port = new FakePort();
	Object.defineProperty(navigator, "serial", { configurable: true, value: {
		requestPort: async () => port, getPorts: async () => [port],
		addEventListener() {}, removeEventListener() {},
	} });
};

(async () => {
	await ask({ op: "new", id: "disc", nid: NID, nif: "A0700190Q", rates: { A: 0, B: 16, C: 5, O: 1, P: 1 } });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });
	const page = await browser.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	global.__page = page;
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "disc", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);

	const login = await page.request.post(`${SITE}/api/method/login`, { data: { usr: CASHIER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());
	await page.goto(`${SITE}/app/dgi-mcf-station`);
	await page.waitForSelector(".dgi-st-gw", { timeout: 30000 });
	await page.selectOption(".dgi-st-gw", "GW-WEB-DISC");
	await page.waitForSelector(".dgi-st-connect", { timeout: 15000 });
	await page.click(".dgi-st-connect");
	await page.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	check((await page.textContent(".dgi-st-device")).includes(NID + "-1"), "MCF inconnu lu par le navigateur (C1h)");
	check(/Proposer ce MCF/.test(await page.textContent(".dgi-st-alert")), "la page invite a proposer le MCF (aucun MCF valide)");
	check(!state().device.status, "aucune fiche MCF avant la proposition");

	await page.click(".dgi-st-propose");
	await page.waitForFunction(() => /propose/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 15000 });
	let s = state();
	check(s.device.status === "Detecte", "MCF propose : etat Detecte, non actif");
	check(s.device.nif === "A0700190Q" && s.device.last_mid === NID + "-1", `NIF et MID lus enregistres (${s.device.nif}, ${s.device.last_mid})`);
	check(s.device.taxpayer_name === "DGI TEST SARL" && /Kinshasa/.test(s.device.taxpayer_address || ""), `contribuable lu en 2Bh (${s.device.taxpayer_name}, ${s.device.taxpayer_address})`);
	check(s.device.discovered_by === CASHIER && !s.pvf_mcf, "proposition tracee, rien n'est rattache au point de vente");
	const sim = await ask({ op: "state", id: "disc" });
	check(sim.invoices.length === 0, "aucune facture sur le MCF (lecture sans effet fiscal)");

	check(bench("dgi_compliance.tests.e2e_station.validate_discovered").includes("VALIDATED=Actif"), "MCF valide par le responsable (autre personne)");
	s = state();
	check(s.pvf_mcf === NID && s.gateway_mcf === NID, "MCF rattache au point de vente et au poste");

	await page.reload();
	await page.waitForSelector(".dgi-st-gw", { timeout: 30000 });
	await page.selectOption(".dgi-st-gw", "GW-WEB-DISC");
	await page.waitForSelector(".dgi-st-test", { timeout: 15000 });
	await page.click(".dgi-st-connect");
	await page.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	await page.click(".dgi-st-test");
	// v5.3.2: the comparisons are made by ERPNext (fiscal.mcf_status.record_desk_test) and shown in the page
	await page.waitForFunction(() => /Test de connexion du MCF/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 15000 });
	const shown = await page.textContent(".dgi-st-alert");
	check(/Reponse du MCF/.test(shown) && /NIF/.test(shown) && /MID/.test(shown), "test de connexion : controles affiches (reponse, NIF, MID...)");
	s = state();
	check(/^(OK|Avertissement) - /.test(s.device.last_connectivity_result || ""), `test de connexion enregistre (${(s.device.last_connectivity_result || "").slice(0, 80)})`);
	await page.screenshot({ path: process.env.SHOT || "/tmp/decouverte_mcf.png", fullPage: true });
	await browser.close();
	py.kill();
	console.log("E2E DECOUVERTE MCF : TOUT EST OK");
})().catch(async (e) => {
	console.error(e);
	try {
		await global.__page.screenshot({ path: (process.env.SHOT || "/tmp/decouverte_mcf.png").replace(".png", "-echec.png"), fullPage: true });
		console.log("[page]", (await global.__page.innerText("body")).slice(0, 1500));
	} catch (x) { /* no page */ }
	py.kill(); process.exit(1);
});
