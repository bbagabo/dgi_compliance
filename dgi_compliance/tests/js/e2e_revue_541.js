/*
 * v5.4.1 - End-to-end test of the corrections of the Poste MCF page, in a real Chromium (Playwright).
 * Same harness as e2e_station.js (serial port = MCF simulator through sim_bridge.py):
 *   A. mistyped enrolment code: clear message, the cashier stays logged in (before: "session expired");
 *   B. operator token lost (PC asleep, server restarted): the poste renews its session and goes on;
 *   C. new browser on an ACTIVE poste: the page offers the enrolment with a new code (key rotation),
 *      the old browser stops with a clear message, the new one starts after the approval.
 *
 *   node e2e_revue_541.js <site_url> <enrolment_code>     (code from tests.e2e_station.prepare)
 * TEST SITES ONLY.
 */
"use strict";
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require("playwright");

const [, , SITE, CODE] = process.argv;
const BENCH = process.env.BENCH || "/home/frappe/frappe-bench";
const CASHIER = "caisse.e2e@example.com", PASSWORD = "Caisse#E2E-2026!", GW = "GW-WEB-E2E";

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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (cond, msg) => { if (!cond) throw new Error("ECHEC : " + msg); console.log("OK  " + msg); };
const gw = () => JSON.parse(bench("dgi_compliance.tests.e2e_station.gateway_state").replace(/^GW=/, ""));

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

async function open_station(browser) {
	const ctx = await browser.newContext();
	const page = await ctx.newPage();
	page.on("pageerror", (e) => console.log("[page error]", e.message));
	await page.exposeFunction("__mcfWrite", async (hex) => (await ask({ op: "write", id: "web", hex })).hex || "");
	await page.addInitScript(FAKE_SERIAL);
	const login = await page.request.post(`${SITE}/api/method/login`, { data: { usr: CASHIER, pwd: PASSWORD } });
	if (login.status() !== 200) throw new Error("login " + login.status());
	await page.goto(`${SITE}/app/dgi-mcf-station`);
	await page.waitForSelector(".dgi-st-gw", { timeout: 30000 });
	await page.selectOption(".dgi-st-gw", GW);
	return page;
}
const logged_user = (page) => page.evaluate(async () => (await (await fetch("/api/method/frappe.auth.get_logged_user")).json()).message);
const step_text = (page) => page.evaluate(() => document.querySelector(".dgi-st-step").innerText);
const alert_text = (page) => page.evaluate(() => document.querySelector(".dgi-st-alert").innerText);

async function normalize_one(label) {
	const out = bench("dgi_compliance.tests.e2e_station.new_invoice");
	const inv = out.match(/INVOICE=(\S+)/)[1];
	let s = {};
	for (let t = 0; t < 30; t++) {
		s = JSON.parse(bench("dgi_compliance.tests.e2e_station.invoice_state", { name: inv }).replace(/^STATE=/, ""));
		if (s.status === "Normalized") break;
		await sleep(2000);
	}
	check(s.status === "Normalized" && s.channel === "MCF", `${label} : ${inv} normalisee par le MCF (${s.code_def})`);
	return inv;
}

(async () => {
	await ask({ op: "new", id: "web", nid: "WEB0000001", nif: "A0700190Q", rates: { A: 0, B: 16, C: 5, O: 1, P: 1 } });
	const browser = await chromium.launch({ args: [`--unsafely-treat-insecure-origin-as-secure=${SITE}`] });

	// ---------------------------------------------------------------- A. mistyped code
	const p1 = await open_station(browser);
	await p1.waitForSelector(".dgi-st-code", { timeout: 15000 });
	await p1.click(".dgi-st-connect");
	await p1.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	await p1.fill(".dgi-st-code", "AAAA-BBBB-CCCC");
	await p1.click(".dgi-st-enrol");
	await p1.waitForFunction(() => /Code d'enrolement refuse/.test(document.body.innerText)
		&& /Enrolement refuse/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 15000 });
	check(true, "A1 code mal saisi : message d'ERPNext affiche et rappel sur la page (" + (await alert_text(p1)).slice(0, 50) + "...)");
	check((await logged_user(p1)) === CASHIER, "A2 la caissiere reste connectee apres un code refuse (plus de 'session expiree')");
	check(!(await p1.evaluate(() => !!(frappe.app && frappe.app.session_expired_dialog && frappe.app.session_expired_dialog.display))), "A3 aucune fenetre 'session expiree'");
	for (let i = 0; i < 5 && (await p1.locator(".modal.show").count()); i++) {      // the cashier closes the message
		await p1.locator(".modal.show .btn-modal-close, .modal.show .close").first().click({ timeout: 3000 }).catch(() => p1.keyboard.press("Escape"));
		await sleep(700);
	}
	await p1.fill(".dgi-st-code", CODE);
	await p1.click(".dgi-st-enrol");
	await p1.waitForFunction(() => /En attente d'approbation/.test(document.querySelector(".dgi-st-step").innerText), null, { timeout: 15000 });
	const fp1 = (await step_text(p1)).match(/[0-9A-F]{4}(-[0-9A-F]{4}){4}/)[0];
	check(true, `A4 le bon code est accepte ensuite, empreinte ${fp1}`);
	check(bench("dgi_compliance.tests.e2e_station.approve", { fingerprint: fp1 }).includes("APPROVED=Active"), "A5 poste approuve par une autre personne");
	await p1.waitForFunction(() => /demarre/.test(document.querySelector(".dgi-st-log").innerText), null, { timeout: 30000 });
	await normalize_one("A6 poste en service");

	// ---------------------------------------------------------------- B. operator token lost
	await p1.evaluate(() => {
		const st = cur_page.page.dgi_station;
		st.station.server.operatorToken = "jeton-perdu";          // as after a long sleep or a server restart
		st.station.wake();
	});
	await p1.waitForFunction(() => /Session du poste renouvelee/.test(document.querySelector(".dgi-st-log").innerText), null, { timeout: 40000 });
	check(true, "B1 jeton operateur perdu : le poste renouvelle sa session seul");
	await normalize_one("B2 apres le renouvellement");
	check(gw().events.includes("OPERATEUR_REFUSE"), "B3 le refus est journalise (OPERATEUR_REFUSE)");

	// ---------------------------------------------------------------- C. new browser on the active poste
	const p2 = await open_station(browser);                       // new profile: no key for this poste
	await p2.waitForFunction(() => /cle d'un autre navigateur/.test(document.querySelector(".dgi-st-step").innerText), null, { timeout: 20000 });
	check(await p2.isVisible(".dgi-st-code"), "C1 nouveau navigateur sur un poste actif : la page propose l'enrolement avec un nouveau code");
	const code2 = bench("dgi_compliance.tests.e2e_station.new_code").replace(/^CODE=/, "");
	check(gw().status === "Active", "C2 le poste reste actif tant que le code n'est pas utilise");
	await p2.click(".dgi-st-connect");
	await p2.waitForSelector(".dgi-st-device table", { timeout: 15000 });
	await p2.fill(".dgi-st-code", code2);
	await p2.click(".dgi-st-enrol");
	await p2.waitForFunction(() => /En attente d'approbation/.test(document.querySelector(".dgi-st-step").innerText), null, { timeout: 15000 });
	const fp2 = (await step_text(p2)).match(/[0-9A-F]{4}(-[0-9A-F]{4}){4}/)[0];
	check(fp2 !== fp1 && gw().status === "A approuver" && gw().key_fingerprint === fp2, `C3 nouvelle cle ${fp2} en attente d'approbation`);
	// the old browser is refused at its next cycle and stops with a clear message
	await p1.evaluate(() => cur_page.page.dgi_station.station && cur_page.page.dgi_station.station.wake());
	await p1.waitForFunction(() => /Le poste MCF est arrete/.test(document.querySelector(".dgi-st-alert").innerText), null, { timeout: 60000 });
	await p1.waitForFunction(() => /n'est PAS celle de ce navigateur/.test(document.querySelector(".dgi-st-step").innerText), null, { timeout: 20000 });
	check(!(await p1.evaluate(() => !!cur_page.page.dgi_station.station)), "C4 l'ancien navigateur s'arrete et l'indique (cle remplacee)");
	await p1.context().close();
	check(bench("dgi_compliance.tests.e2e_station.approve", { fingerprint: fp2 }).includes("APPROVED=Active"), "C5 nouvelle cle approuvee par une autre personne");
	await p2.waitForFunction(() => /demarre/.test(document.querySelector(".dgi-st-log").innerText), null, { timeout: 30000 });
	await normalize_one("C6 nouveau navigateur en service");
	const sim = await ask({ op: "state", id: "web" });
	check(sim.invoices.length === 3, "C7 le MCF a enregistre exactement 3 factures (aucun doublon)");
	await p2.screenshot({ path: process.env.SHOT || "/tmp/poste_mcf_541.png", fullPage: true });
	await browser.close();
	py.kill();
	console.log("TOUT EST OK");
})().catch((e) => { console.error(e.message || e); py.kill(); process.exit(1); });
