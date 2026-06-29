module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire clr_i,
    input wire set_i,
    input wire en_i,
    input wire valid_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (clr_i && en_i && !set_i) |-> ##1 !valid_q);

endmodule


bind keymgr_sideload_key sva_checker sva_checker_inst (.*);
