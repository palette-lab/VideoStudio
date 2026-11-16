"""FoodActionLibrary: the physical cooking actions a scene can show, each with its real choreography and physics.

Built from the ChefVision AI Enhanced Food Cinematic Master Prompt (sections 3-4, 10) plus the action list of the food
engine brief. The recipe understanding step picks the action ids a recipe really needs; only those actions' text goes
into the planner prompt (never the whole library, never every action).
"""

# id -> (group, choreography: hand + tool + ingredient + contact + movement + resistance + visible result)
ACTIONS: dict[str, tuple[str, str]] = {
    # A. Ingredient preparation
    "wash": ("prep", "the ingredient is held under a gentle stream of water and turned to rinse every side; droplets "
                     "bead and run off; it is lifted out dripping"),
    "rinse_grain": ("prep", "rice, lentils or grains are swirled with fingers in a bowl of water, the water turns cloudy, "
                            "is tipped out while a hand holds the grains back, and refilled until it runs clearer"),
    "dry": ("prep", "the wet ingredient is placed on a towel and patted dry; the surface changes from glossy wet to matte"),
    "peel": ("prep", "fingers steady the ingredient while a peeler or knife takes off a thin strip; the peel curls away "
                     "and drops onto the board"),
    "chop": ("prep", "the knife tip rests on the board, the blade rocks down in controlled strokes, the other hand's "
                     "curled fingertips guide the ingredient; pieces of one consistent size collect in a pile"),
    "dice": ("prep", "the ingredient is cut into planks, then sticks, then even cubes; the cubes are pushed together "
                     "with the flat of the blade"),
    "slice": ("prep", "the blade passes through the ingredient in deliberate strokes; even slices of visible thickness "
                      "fall away and fan out on the board"),
    "julienne": ("prep", "slices are stacked and cut into thin matchsticks of one length"),
    "grate": ("prep", "the ingredient is pushed across the grater in short repeated strokes; fine shreds pile up "
                      "beneath it and the piece in the hand gets smaller"),
    "zest": ("prep", "citrus is dragged over a fine zester; bright flecks of zest fall, the white pith is not reached"),
    "crush": ("prep", "the flat of a knife or a pestle presses down; the ingredient cracks and breaks into smaller "
                      "pieces that stay visible rather than disappearing"),
    "mince": ("prep", "the knife rocks back and forth over the pile, gathering and re-cutting it into fine pieces"),
    "measure": ("prep", "the ingredient is spooned or scooped into a measuring cup or spoon and levelled with a straight "
                        "edge; the excess falls back into the container"),
    "open_package": ("prep", "the seal, lid, can, husk or wrapper is physically opened by hand before the contents are used"),
    "debone_clean": ("prep", "meat or fish is trimmed on the board: the knife follows the bone or removes fat and skin, "
                             "the trimmed piece is set aside"),
    "soak": ("prep", "the ingredient is placed in a bowl of water and left; a time transition shows it plumped"),
    # B. Cracking, opening and separating
    "crack_egg": ("handle", "the egg is held over a bowl, tapped once on a firm edge, opened with both thumbs; white and "
                            "yolk fall in naturally and the two shell halves stay in the fingers"),
    "separate_egg": ("handle", "the yolk is passed between the shell halves (or a separator); the white streams into the "
                               "bowl below while the yolk stays whole"),
    "remove_shell": ("handle", "a fallen shell fragment is scooped out with a fingertip or a shell half - it never vanishes"),
    # C. Pouring, adding, layering
    "pour_liquid": ("handle", "the real container is tilted; a continuous stream of realistic thickness and speed lands, "
                              "splashes slightly and settles; the container returns upright"),
    "sprinkle": ("handle", "fingers or a spoon release a controlled sprinkle; individual particles fall and dust the surface"),
    "sift": ("handle", "flour or powder is tapped through a sieve; a fine cloud falls and forms a soft mound"),
    "dollop": ("handle", "a thick ingredient leaves the spoon as a slow ribbon or dollop that folds onto itself"),
    "place_ingredient": ("handle", "each piece is placed with fingers, tongs or a spoon; it touches the food and stays where put"),
    "distribute_toppings": ("handle", "toppings are scattered piece by piece across the surface, evenly, edges included"),
    "spread": ("handle", "a knife or spoon back pushes butter or sauce outward in arcs; the layer thins and evens out"),
    "apply_sauce": ("handle", "sauce is ladled into the centre and spiralled outward with the ladle's base, leaving a rim"),
    "brush": ("handle", "a pastry brush dipped in butter, oil or egg wash sweeps over the surface, leaving a glossy coat"),
    "layer": ("handle", "components are added in visible layers in the recipe's order; each layer covers the one below"),
    "marinate": ("handle", "the ingredient is turned in the marinade with hands or a spoon until coated, then covered; a "
                           "time transition shows the rest"),
    "season": ("handle", "salt, pepper or spice is pinched and dropped from a height so it spreads evenly"),
    # D. Mixing and dough work
    "whisk": ("mix", "the whisk wires touch the bowl; the wrist makes quick circular strokes; the mixture changes "
                     "gradually from separate to combined, paler, thicker or foamy"),
    "beat": ("mix", "a spoon or electric beater works the mixture hard; it lightens in colour and gains volume"),
    "stir": ("mix", "the spoon follows one steady path around the vessel; ingredients move together and settle"),
    "fold": ("mix", "the spatula cuts down through the centre, sweeps the bottom and lifts the mixture over itself; "
                    "streaks remain until they just disappear"),
    "blend": ("mix", "the lid is pressed on, the blender runs, the contents swirl into a vortex and turn smooth"),
    "knead": ("mix", "the heels of the palms push the dough away, fold it back, turn it a quarter and repeat; it gets "
                     "smoother and springs back"),
    "rest_dough": ("mix", "the dough is covered in a bowl; a time transition shows it risen and puffed"),
    "roll": ("mix", "the rolling pin moves forward and back with even pressure; the dough thins progressively and is "
                    "turned between passes"),
    "shape": ("mix", "hands form the portion: pinch, fold, roll into balls, press patties or seal edges; the seam is "
                     "placed underneath"),
    # E. Cooking and heat
    "heat_pan": ("cook", "the pan goes on the hob before anything is added; a subtle heat shimmer rises from it"),
    "heat_oil": ("cook", "oil is poured into the hot pan, spreads, and begins to shimmer and ripple"),
    "temper": ("cook", "whole spices hit the hot oil, crackle and pop, and darken slightly within seconds"),
    "fry": ("cook", "food is lowered into hot fat, bubbles erupt around it, it sizzles and colours gradually; it is "
                    "turned with a utensil"),
    "deep_fry": ("cook", "pieces are lowered on a spider or slotted spoon into deep oil; vigorous bubbles, steady golden "
                         "browning, then lifted out dripping and drained"),
    "saute": ("cook", "ingredients are tossed or stirred in a little hot fat; edges soften, turn translucent and brown"),
    "stir_fry": ("cook", "a wok over high flame; food is tossed with a ladle and spatula in quick, constant motion"),
    "boil": ("cook", "a rolling boil: large bubbles break the whole surface; steam rises"),
    "simmer": ("cook", "a gentle simmer: small lazy bubbles at the edges, the sauce slowly thickens and reduces"),
    "steam": ("cook", "food sits in a steamer basket over boiling water; the lid is lifted and a cloud of steam escapes"),
    "dum_cook": ("cook", "a DEEP heavy handi (taller than wide-rimmed pan, the layers well below its rim) gets a lid of "
                         "the SAME diameter that rests ON THE RIM, never on the food, so the food is hidden; a rope of "
                         "dough is pressed along the seam where lid meets rim, all the way round, closing the gap; it "
                         "cooks on low heat; a time transition, then the baked dough is cracked along the seam, the lid "
                         "lifts off the rim and steam escapes"),
    "bake": ("cook", "the tray is carried to the oven, set on the rack, the door closes; the cooked result appears only "
                     "after an explicit time transition"),
    "roast": ("cook", "food goes into a hot oven or over a flame; it browns and blisters gradually over a time transition"),
    "grill": ("cook", "food is laid on the hot grate, sizzles on contact, gets grill marks and is turned with tongs"),
    "sear": ("cook", "food is pressed onto a very hot pan, a hard sizzle, a deep brown crust forms; it is flipped once"),
    "toast": ("cook", "bread or buns lie cut side down on a hot surface and turn evenly golden"),
    "melt": ("cook", "butter, chocolate, cheese or sugar softens at the edges and flows progressively, keeping its "
                     "real viscosity"),
    "caramelize": ("cook", "sugar or onions slowly darken from pale to amber to deep brown with constant stirring"),
    "reduce": ("cook", "the liquid level visibly drops and the sauce coats the back of a spoon"),
    "flip": ("cook", "a spatula slides under the food, lifts and turns it in one motion; it lands browned side up"),
    "blanch": ("cook", "vegetables go into boiling water briefly, then into ice water; the colour brightens"),
    "chill_set": ("cook", "the dish goes into the fridge or freezer; a time transition shows it set or firm"),
    # F. Finishing, serving, eating
    "remove_from_heat": ("finish", "the pan is lifted off the hob, or the tray out of the oven with mitts, and set on a trivet"),
    "drain": ("finish", "food is tipped into a colander or lifted onto a rack/paper; liquid or oil drips off and pools"),
    "rest": ("finish", "cooked food sits on the board, rack or pan; steam gradually lessens and it settles"),
    "cool": ("finish", "the dish cools on a rack; a time transition, the steam is gone"),
    "unmould": ("finish", "a knife loosens the edge, the ring or tin is released and lifted away without damaging the food"),
    "cut_portion": ("finish", "the other hand or a utensil steadies the food; the knife presses through crust and filling "
                              "in deliberate strokes, leaves the cut, and the portion separates"),
    "scoop": ("finish", "a spoon, scoop or ladle enters the food, gathers a real portion and lifts it with its weight"),
    "lift_transfer": ("finish", "a spatula or tongs slide under the portion, support it, carry it along a visible path to "
                                "the plate and release it; it lands and settles"),
    "plate": ("finish", "the main portion goes down first, then sides, garnish and sauce, deliberately and in place"),
    "garnish": ("finish", "herbs, seeds or powder are placed or sprinkled from fingertips onto the finished dish"),
    "drizzle": ("finish", "sauce is spooned or squeezed in a controlled stream, leaving a line, pool or drip on the food"),
    "assemble": ("finish", "components are stacked in order with hands - base, filling, sauce, top - and pressed lightly"),
    "wrap_roll": ("finish", "a sheet or mat is rolled tight around the filling with even pressure; the edge is sealed"),
    "pour_drink": ("finish", "the drink is poured into the glass in a steady stream; it swirls, foams or layers and settles"),
    "texture_hero": ("finish", "a close look at a real texture the recipe has: a crisp crack, flaky layers, a molten "
                               "centre, a cheese pull, bubbling sauce or a clean cross-section"),
    "first_bite": ("finish", "a fork, spoon or hand breaks off a real portion, lifts it toward the camera and reveals the "
                             "inside; the rest of the dish stays consistent"),
}

GROUPS = {"prep": "Ingredient preparation", "handle": "Ingredient handling", "mix": "Mixing and dough work",
          "cook": "Cooking and heat", "finish": "Finishing, serving and eating"}

# Actions that need a real cut / serve / taste somewhere in a recipe that has them in its workflow.
FINISHING = {"cut_portion", "lift_transfer", "plate", "scoop", "first_bite", "pour_drink", "garnish", "drizzle"}
# Actions that take time: the cooked / risen / set result needs an explicit time transition.
TIMED = {"bake", "roast", "rest_dough", "marinate", "soak", "dum_cook", "chill_set", "cool", "simmer", "reduce"}

CATEGORIES = ["Indian", "Chinese", "Japanese", "Korean", "Italian", "Continental", "Fast food", "Street food",
              "Bakery", "Cakes and desserts", "Snacks", "Vegetarian", "Non-vegetarian", "Beverages", "Healthy",
              "Fusion", "Other"]

# Ingredient state progression (VisualContinuityManager): a later scene may not go back to an earlier rank.
STATE_RANKS = [
    (0, ("whole", "raw", "uncut", "unpeeled", "in its package", "packaged", "sealed", "dry grains", "uncooked rice")),
    (1, ("washed", "rinsed", "soaked", "drained grains")),
    (2, ("peeled", "trimmed", "cleaned", "measured", "cracked", "separated")),
    (3, ("sliced", "chopped", "diced", "minced", "grated", "cut", "julienned", "crushed", "zested", "partially sliced")),
    (4, ("marinated", "mixed", "whisked", "combined", "seasoned", "batter", "dough", "kneaded", "blended", "folded",
         "coated")),
    (5, ("shaped", "rolled", "layered", "assembled", "topped", "in the pan", "in the tin", "on the tray", "risen")),
    (6, ("frying", "sizzling", "cooking", "simmering", "boiling", "baking", "roasting", "grilling", "steaming",
         "browning", "melting", "caramelizing")),
    (7, ("fried", "cooked", "baked", "roasted", "grilled", "steamed", "browned", "melted", "caramelized", "set",
         "golden", "crisp", "toasted", "done")),
    (8, ("rested", "cooled", "unmoulded", "drained")),
    (9, ("portioned", "sliced portion", "served", "scooped", "transferred")),
    (10, ("plated", "garnished", "drizzled", "poured into the glass")),
    (11, ("bitten", "eaten", "tasted", "half-eaten")),
]


def rank(state: str) -> int | None:
    """The progression rank of a state description (the highest rank whose word it contains), None if unknown."""
    s = f" {(state or '').lower()} "
    best = None
    for r, words in STATE_RANKS:
        if any(f" {w}" in s for w in words):
            best = r
    return best


def choreography(action_ids: list[str]) -> str:
    """The choreography of only the given actions, grouped, for the planner prompt."""
    by_group: dict[str, list[str]] = {}
    for a in dict.fromkeys(action_ids):
        if a in ACTIONS:
            g, text = ACTIONS[a]
            by_group.setdefault(g, []).append(f"  - {a}: {text}.")
    return "\n".join(f"{GROUPS[g]}:\n" + "\n".join(lines) for g, lines in by_group.items() if lines)
