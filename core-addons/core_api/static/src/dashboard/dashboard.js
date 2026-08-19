import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { getColor } from "@web/core/colors/colors";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const LOG_VIEWS = [
    [false, "list"],
    [false, "form"],
];
// the breakdown key each mode groups by, server-side and in the click domain
const BREAKDOWN_FIELD = { module: "module_id", endpoint: "endpoint_id", path: "name" };

export class ApiDashboard extends Component {
    static template = "core_api.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvasRef = useRef("chart");
        this.state = useState({
            windows: [],
            breakdown: [],
            breakdown_by: "module",
            module_options: [],
            module_id: null,
            since: false,
        });

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load(null);
        });
        onMounted(() => this.renderChart());
        onPatched(() => {
            if (this.chartDirty) {
                this.chartDirty = false;
                this.renderChart();
            }
        });
        onWillUnmount(() => this.chart?.destroy());
    }

    async load(moduleId) {
        Object.assign(
            this.state,
            await this.orm.call("core.api.log", "dashboard_data", [moduleId])
        );
    }

    /** "" is All modules; "false" is the no-module bucket, not id 0.
     *
     * The <option> values are built here, not in the template: OWL only
     * whitelists a fixed set of globals in template expressions (see
     * RESERVED_WORDS in owl.js) and the String constructor is not one of
     * them, so calling it there resolves against the component and throws.
     */
    get selectValue() {
        const id = this.state.module_id;
        return id === null || id === undefined ? "" : `${id}`;
    }

    get moduleOptions() {
        return this.state.module_options.map((opt) => ({
            value: `${opt.id}`,
            label: opt.label,
        }));
    }

    async onModuleChange(ev) {
        const raw = ev.target.value;
        const moduleId = raw === "" ? null : raw === "false" ? false : parseInt(raw, 10);
        this.chartDirty = true; // rebuild once the template has the new canvas
        await this.load(moduleId);
    }

    get moduleLabel() {
        const id = this.state.module_id;
        if (id === null || id === undefined) {
            return null;
        }
        return this.state.module_options.find((o) => o.id === id)?.label || "No module";
    }

    color(index) {
        return getColor(index, cookie.get("color_scheme"), this.state.breakdown.length);
    }

    renderChart() {
        this.chart?.destroy();
        this.chart = null;
        // the canvas is t-if'd out on the empty state
        if (!this.canvasRef.el) {
            return;
        }
        const colors = this.state.breakdown.map((r, i) => this.color(i));
        this.chart = new Chart(this.canvasRef.el, {
            type: "doughnut",
            data: {
                labels: this.state.breakdown.map((r) => r.label),
                datasets: [
                    {
                        data: this.state.breakdown.map((r) => r.count),
                        backgroundColor: colors,
                        hoverBackgroundColor: colors,
                    },
                ],
            },
            options: {
                maintainAspectRatio: false,
                plugins: { legend: { display: false } }, // the table is the legend
                onClick: (ev, items) => items.length && this.onRowClick(this.state.breakdown[items[0].index]),
            },
        });
    }

    _openLogs(name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "core.api.log",
            views: LOG_VIEWS,
            domain,
        });
    }

    /** Every click-through carries the module filter and says so. */
    _scope() {
        const label = this.moduleLabel;
        return {
            domain: label === null ? [] : [["module_id", "=", this.state.module_id]],
            suffix: label === null ? "" : ` – ${label}`,
        };
    }

    onWindowClick(win, state) {
        const scope = this._scope();
        const domain = [["create_date", ">=", win.since], ...scope.domain];
        let name = `API calls${scope.suffix} – last ${win.days} day${win.days > 1 ? "s" : ""}`;
        if (state) {
            domain.push(["state", "=", state]);
            name += state === "success" ? " (success)" : " (errors)";
        }
        this._openLogs(name, domain);
    }

    onRowClick(row) {
        // "Other" folds several rows together: no domain can express it
        if (row.label === "Other") {
            return;
        }
        const scope = this._scope();
        const field = BREAKDOWN_FIELD[this.state.breakdown_by];
        // in module mode the scope is empty, so this reads the same either way
        const name = `API calls${scope.suffix} – ${row.label} – last 30 days`;
        this._openLogs(name, [
            ["create_date", ">=", this.state.since],
            ...scope.domain,
            [field, "=", row.id],
        ]);
    }
}

registry.category("actions").add("core_api_dashboard", ApiDashboard);
