import { PR_COMPLIANCE_URL } from "../content";
import { Chain } from "./Chain";
import { Kit } from "./Kit";
import { Policy } from "./Policy";

export function Compliance() {
  return (
    <section className="section" id="compliance" aria-labelledby="compliance-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="compliance-title">Every altered photo says so.</h2>
          <p className="lede">
            Since 1 January 2026, California’s AB 723 has required real-estate agents who advertise with a digitally
            altered photo to say so next to it and to link to the unaltered original. ShootAgent records what every edit
            changed, discloses what the law counts as an alteration, and declines the edits no listing should carry.
          </p>
          <p className="section-note">
            These tools are in review in <a href={PR_COMPLIANCE_URL}>pull request #1</a> and not yet in the live demo.
          </p>
        </div>

        <div className="part">
          <Kit />
        </div>

        <div className="part part-stack">
          <div className="part-copy">
            <h3>A record anyone can check.</h3>
            <p className="body">
              Each photo’s record is a chain of SHA-256 fingerprints, from the camera’s brackets to the file on the
              listing. Change one byte of a photo and its entry fails; rewrite the entry to match, and the link from the
              next one fails. These are this page’s own image files, fingerprinted in your browser.
            </p>
          </div>
          <Chain />
        </div>

        <div className="part part-split">
          <div className="part-copy">
            <h3>Some edits it won’t make.</h3>
            <p className="body">
              The listing photo policy checks every customer request and chat edit. Hiding the property’s condition or its
              surroundings is declined, with the reason. Staging, a new sky or a removed bin is made and disclosed.
              Exposure and colour correction need no disclosure.
            </p>
            <p className="body">
              It is stricter than AB 723, which allows removing power lines with a disclosure: it follows MLS photo rules
              and the NAR’s “true picture” standard.
            </p>
            <p className="body muted">This box runs a copy of the product’s policy, checked against it request by request.</p>
          </div>
          <Policy />
        </div>
      </div>
    </section>
  );
}
