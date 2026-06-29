module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire valid,
    input wire adv_en_i,
    input wire inputs_invalid_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (valid && !adv_en_i && inputs_invalid_q[0]) |=> $stable(inputs_invalid_q[0]));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
