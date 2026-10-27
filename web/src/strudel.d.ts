// The <strudel-editor> custom element that @strudel/repl registers (its module declaration: strudel-module.d.ts).
import type { DetailedHTMLProps, HTMLAttributes } from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "strudel-editor": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & { code?: string };
    }
  }
}
