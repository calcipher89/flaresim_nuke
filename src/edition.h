// edition.h — node class and module names for this build.
//
// A normal build makes the FlareSimPlus / FlareSimPlus3D nodes.  A side-by-side
// build (cmake -DFLARESIM_EDITION=Beta) adds the edition to every name, so it
// makes FlareSimPlusBeta / FlareSimPlusBeta3D, calls the FlareSimPlusBeta_*.py
// modules and is labelled "FlareSim+ Beta".  Both builds can then load in the
// same Nuke session without one taking the other's nodes.

#pragma once

#ifndef FSP_EDITION
#define FSP_EDITION ""
#endif

#ifndef FSP_EDITION_LABEL
#define FSP_EDITION_LABEL ""   // " Beta" for FSP_EDITION "Beta"
#endif

#define FSP_CLASS_2D  "FlareSimPlus" FSP_EDITION
#define FSP_CLASS_3D  "FlareSimPlus" FSP_EDITION "3D"
#define FSP_LABEL     "FlareSim+" FSP_EDITION_LABEL
#define FSP_MODULE(m) "FlareSimPlus" FSP_EDITION "_" m
