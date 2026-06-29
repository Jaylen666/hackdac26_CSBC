module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire cnt,
    input wire LastAdvRound,
    input wire LastIdRound,
    input wire LastGenRound,
    input wire adv_sel,
    input wire id_sel,
    input wire gen_sel,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (adv_sel |-> cnt <= LastAdvRound) and (id_sel |-> cnt <= LastIdRound) and (gen_sel |-> cnt <= LastGenRound));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
