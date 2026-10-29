// From the README's results, measured on the submitted code (docs/eval in the repository).
const FAMILIES: [string, number, number, number, number][] = [
  ["Ghosting, wrong grouping", 21, 21, 0, 0],
  ["Camera, tripod, person removal", 14, 4, 0, 10],
  ["Fireplace", 7, 1, 2, 4],
  ["Paint colour", 6, 4, 0, 2],
  ["Window, sky", 4, 0, 0, 4],
  ["Lens geometry", 4, 0, 0, 4],
  ["Floor colour", 2, 1, 1, 0],
  ["TV, art", 1, 0, 0, 1],
];
const total = (i: 1 | 2 | 3 | 4) => FAMILIES.reduce((s, f) => s + f[i], 0);

export function Results() {
  return (
    <section className="section" id="results" aria-labelledby="results-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="results-title">Measured, misses included.</h2>
          <p className="lede">
            One full run of the agent over every case in AutoHDR’s defect set: 59 labelled defects and 4 clean photos.
            A fix counts only if the agent confirmed the labelled defect over the labelled region and kept its fix; edits
            that fail their self-check are undone.
          </p>
        </div>
        <dl className="tape">
          <div>
            <dt>Defects fixed</dt>
            <dd>
              {total(2)} <small>of {total(1)}</small>
            </dd>
          </div>
          <div>
            <dt>Ghosting and grouping</dt>
            <dd>
              21 <small>of 21</small>
            </dd>
          </div>
          <div>
            <dt>Clean photos edited</dt>
            <dd>
              0 <small>of 4</small>
            </dd>
          </div>
          <div>
            <dt>QC per photo</dt>
            <dd>
              $0.07 <small>and 39 s</small>
            </dd>
          </div>
        </dl>
        <div className="results-grid">
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Results by defect family</caption>
              <thead>
                <tr>
                  <th scope="col">Defect family</th>
                  <th scope="col">Cases</th>
                  <th scope="col">Fixed</th>
                  <th scope="col">Flagged</th>
                  <th scope="col">Missed</th>
                </tr>
              </thead>
              <tbody>
                {FAMILIES.map(([name, cases, fixed, flagged, missed]) => (
                  <tr key={name} data-strong={fixed === cases || undefined}>
                    <th scope="row">{name}</th>
                    <td>{cases}</td>
                    <td>{fixed}</td>
                    <td>{flagged}</td>
                    <td>{missed}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">All defects</th>
                  <td>{total(1)}</td>
                  <td>{total(2)}</td>
                  <td>{total(3)}</td>
                  <td>{total(4)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <div className="results-notes">
            <p>
              <b>Customer requests.</b> All 7 saved requests we ran from AutoHDR’s editor dataset were applied (a floor colour, a
              TV picture, wall paint, four removals), and the pixels outside every edit were byte-identical.
            </p>
            <p>
              <b>Removals.</b> On 12 real photos with bins, cars and cords, gpt-image-2 inside a grown, shadow-aware mask
              passes the vision judge on 10 to 11 of them; LaMa alone passes 2.
            </p>
            <p>
              <b>Misses.</b> In 12 the detector raised no candidate, including all four lens cases; in 13 the agent
              didn’t confirm the one it raised. The 3 flagged include a firebox restore the judge rejected. Results move
              by a few cases between runs: the run before fixed 30, including the erased stop sign at the top of this
              page, which this run missed.
            </p>
            <p className="muted">
              One run on an RTX 4060 laptop, with Claude Sonnet 5.5 as the agent and the judge. Costs are estimates from
              token counts at list prices.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
