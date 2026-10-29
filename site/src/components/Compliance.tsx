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
            Since 1 January 2026, California’s AB 723 requires a digitally altered listing photo to say so and to link to
            the unaltered original. ShootAgent records what every edit changed, discloses what the law counts as an
            alteration, and declines the edits no listing should carry.
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
              listing. Change one byte anywhere and the chain breaks where it changed. These are this page’s own image
              files, fingerprinted in your browser.
            </p>
          </div>
          <Chain />
        </div>

        <div className="part part-split">
          <div className="part-copy">
            <h3>Some edits it won’t make.</h3>
            <p className="body">
              The listing photo policy runs before every edit. Hiding the property’s condition or its surroundings is
              declined, with the reason. Staging, a new sky or a removed bin is made and disclosed. Exposure and colour
              correction need no disclosure.
            </p>
            <p className="body muted">This box runs an abridged copy of the product’s policy.</p>
          </div>
          <Policy />
        </div>
      </div>
    </section>
  );
}
